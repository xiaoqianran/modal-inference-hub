"""Pure image and connected-component operations."""
from __future__ import annotations

from collections import OrderedDict
import hashlib
import io
import threading
import time
import numpy as np
from scipy import ndimage
from PIL import Image

CANONICAL_SIZE = 1024
_BBOX_ALPHA_THRESHOLD = 8
_COMPONENT_ALPHA_THRESHOLD = 8
_COMPONENT_MIN_PIXELS = 64
_COMPONENT_RELATIVE_MIN = 0.0005
_COMPONENT_LIMIT = 64
_selection_cache_lock = threading.RLock()
_selection_cache: OrderedDict[str, tuple[Image.Image, np.ndarray, int]] = OrderedDict()
_selection_cache_bytes = 0
_SELECTION_CACHE_LIMIT = 4
_SELECTION_CACHE_MAX_BYTES = 64 * 1024 * 1024

def _label_components(mask: Image.Image) -> tuple[np.ndarray, np.ndarray, int]:
    alpha = np.asarray(mask.convert("L"), dtype=np.uint8)
    binary = alpha > _COMPONENT_ALPHA_THRESHOLD
    labels, count = ndimage.label(binary, structure=np.ones((3, 3), dtype=np.uint8))
    return alpha, labels.astype(np.int32, copy=False), int(count)


def analyze_components(matte: Image.Image | bytes) -> dict:
    if isinstance(matte, bytes):
        with Image.open(io.BytesIO(matte)) as opened:
            rgba = opened.convert("RGBA")
    else:
        rgba = matte.convert("RGBA")
    alpha, labels, count = _label_components(rgba.getchannel("A"))
    foreground_pixels = int(np.count_nonzero(alpha > _COMPONENT_ALPHA_THRESHOLD))
    if foreground_pixels <= 0:
        raise ValueError("rembg 未检测到可用前景")
    if count <= 0:
        raise ValueError("未检测到连通前景组件")

    areas = np.bincount(labels.ravel(), minlength=count + 1)
    largest = int(areas[1:].max(initial=0))
    minimum = max(_COMPONENT_MIN_PIXELS, int(round(largest * _COMPONENT_RELATIVE_MIN)))
    slices = ndimage.find_objects(labels, max_label=count)
    components: list[dict] = []
    ignored_pixels = 0
    ignored_count = 0
    for label_id in range(1, count + 1):
        area = int(areas[label_id])
        region = slices[label_id - 1] if label_id - 1 < len(slices) else None
        if region is None:
            continue
        yslice, xslice = region
        if area < minimum:
            ignored_count += 1
            ignored_pixels += area
            continue
        components.append(
            {
                "id": f"cc-{label_id:05d}",
                "label": label_id,
                "bbox": [int(xslice.start), int(yslice.start), int(xslice.stop), int(yslice.stop)],
                "area_pixels": area,
                "foreground_ratio": area / foreground_pixels,
                "image_ratio": area / (rgba.width * rgba.height),
                "selected": True,
            }
        )

    components.sort(key=lambda item: item["area_pixels"], reverse=True)
    if len(components) > _COMPONENT_LIMIT:
        overflow = components[_COMPONENT_LIMIT:]
        ignored_count += len(overflow)
        ignored_pixels += sum(int(item["area_pixels"]) for item in overflow)
        components = components[:_COMPONENT_LIMIT]

    if not components:
        # A tiny valid foreground is still selectable rather than disappearing from the UI.
        label_id = int(np.argmax(areas[1:]) + 1)
        region = slices[label_id - 1]
        assert region is not None
        yslice, xslice = region
        area = int(areas[label_id])
        components = [
            {
                "id": f"cc-{label_id:05d}",
                "label": label_id,
                "bbox": [int(xslice.start), int(yslice.start), int(xslice.stop), int(yslice.stop)],
                "area_pixels": area,
                "foreground_ratio": area / foreground_pixels,
                "image_ratio": area / (rgba.width * rgba.height),
                "selected": True,
            }
        ]
        ignored_count = max(0, count - 1)
        ignored_pixels = max(0, foreground_pixels - area)

    return {
        "source_size": [rgba.width, rgba.height],
        "components": components,
        "component_count": len(components),
        "raw_component_count": count,
        "ignored_component_count": ignored_count,
        "ignored_foreground_pixels": ignored_pixels,
        "foreground_pixels": foreground_pixels,
        "minimum_component_pixels": minimum,
    }


def _selection_inputs(matte_bytes: bytes) -> tuple[Image.Image, np.ndarray]:
    global _selection_cache_bytes
    key = hashlib.sha256(matte_bytes).hexdigest()
    with _selection_cache_lock:
        cached = _selection_cache.get(key)
        if cached is not None:
            _selection_cache.move_to_end(key)
            return cached[0].copy(), cached[1]

    with Image.open(io.BytesIO(matte_bytes)) as opened:
        rgba = opened.convert("RGBA")
    _, labels, _ = _label_components(rgba.getchannel("A"))
    estimated_bytes = rgba.width * rgba.height * 4 + int(labels.nbytes)
    if estimated_bytes <= _SELECTION_CACHE_MAX_BYTES:
        with _selection_cache_lock:
            while _selection_cache and (
                len(_selection_cache) >= _SELECTION_CACHE_LIMIT
                or _selection_cache_bytes + estimated_bytes > _SELECTION_CACHE_MAX_BYTES
            ):
                _, (_, _, evicted_bytes) = _selection_cache.popitem(last=False)
                _selection_cache_bytes -= evicted_bytes
            _selection_cache[key] = (rgba.copy(), labels, estimated_bytes)
            _selection_cache_bytes += estimated_bytes
    return rgba, labels


def clear_selection_cache() -> None:
    global _selection_cache_bytes
    with _selection_cache_lock:
        _selection_cache.clear()
        _selection_cache_bytes = 0


def canonicalize_components(
    matte_bytes: bytes,
    selected_component_ids: list[str],
    component_state: dict | None = None,
) -> dict:
    started = time.perf_counter()
    if component_state is not None:
        rgba, labels = _selection_inputs(matte_bytes)
        analysis = component_state
    else:
        with Image.open(io.BytesIO(matte_bytes)) as opened:
            rgba = opened.convert("RGBA")
        analysis = analyze_components(rgba)
        _, labels, _ = _label_components(rgba.getchannel("A"))
    available = {item["id"]: item for item in analysis["components"]}
    selected = list(dict.fromkeys(selected_component_ids))
    unknown = [item for item in selected if item not in available]
    if unknown:
        raise ValueError(f"未知前景组件: {', '.join(unknown)}")
    if not selected:
        raise ValueError("至少保留一个前景组件")

    all_ids = list(available)
    if set(selected) == set(all_ids):
        filtered = rgba
    else:
        alpha = np.asarray(rgba.getchannel("A"), dtype=np.uint8)
        selected_labels = [
            int(available[item].get("label", int(item.split("-")[-1])))
            for item in selected
        ]
        keep = np.isin(labels, np.asarray(selected_labels, dtype=np.int32))
        filtered_alpha = np.where(keep, alpha, 0).astype(np.uint8)
        filtered = rgba.copy()
        filtered.putalpha(Image.fromarray(filtered_alpha, mode="L"))

    bbox = _foreground_bbox(filtered.getchannel("A"))
    canonical = _letterbox_rgba(filtered, bbox)
    selection_bytes = _png_bytes(filtered, compress_level=1)
    # Keep canonical encoding identical to the initial rembg path so restoring
    # the same semantic selection restores the same content hash.
    canonical_bytes = _png_bytes(canonical)
    visible = []
    for item in analysis["components"]:
        updated = dict(item)
        updated["selected"] = item["id"] in selected
        updated.pop("label", None)
        visible.append(updated)
    return {
        "selection_bytes": selection_bytes,
        "canonical_bytes": canonical_bytes,
        "canonical_sha256": hashlib.sha256(canonical_bytes).hexdigest(),
        "source_size": analysis["source_size"],
        "foreground_bbox": list(bbox),
        "selected_component_ids": selected,
        "components": visible,
        "component_count": analysis["component_count"],
        "raw_component_count": analysis.get("raw_component_count", analysis["component_count"]),
        "ignored_component_count": analysis.get("ignored_component_count", 0),
        "ignored_foreground_pixels": analysis.get("ignored_foreground_pixels", 0),
        "minimum_component_pixels": analysis.get("minimum_component_pixels", _COMPONENT_MIN_PIXELS),
        "selection_elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
    }


def _foreground_bbox(mask: Image.Image) -> tuple[int, int, int, int]:
    binary = mask.point(lambda value: 255 if value > _BBOX_ALPHA_THRESHOLD else 0)
    bbox = binary.getbbox()
    if bbox is None:
        raise ValueError("rembg 未检测到可用前景")
    return bbox


def _letterbox_rgba(
    rgba: Image.Image,
    bbox: tuple[int, int, int, int],
    size: int = CANONICAL_SIZE,
) -> Image.Image:
    crop = rgba.crop(bbox)
    width, height = crop.size
    if width <= 0 or height <= 0:
        raise ValueError("前景包围盒无效")
    scale = min(size / width, size / height)
    target = (max(1, round(width * scale)), max(1, round(height * scale)))
    resized = crop.resize(target, Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    offset = ((size - target[0]) // 2, (size - target[1]) // 2)
    canvas.alpha_composite(resized, offset)
    return canvas


def _png_bytes(image: Image.Image, compress_level: int = 6) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG", compress_level=compress_level)
    return output.getvalue()
