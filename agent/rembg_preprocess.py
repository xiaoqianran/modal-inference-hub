"""Local-only image matting and canonicalization for modal-3D.

The source image never leaves the machine during preprocessing. The active V1
pipeline is intentionally small: rembg/birefnet-general produces one global
alpha matte, then the foreground bounding box is letterboxed to the cloud
contract (1024x1024, 8-bit RGBA PNG).
"""

from __future__ import annotations

import hashlib
import io
import os
import threading
import time

import numpy as np
from scipy import ndimage
from pathlib import Path

from PIL import Image, ImageOps

from agent.storage import data_dir

ENGINE = "birefnet-general"
CANONICAL_SIZE = 1024
_BBOX_ALPHA_THRESHOLD = 8
_session_lock = threading.RLock()
_session = None

_COMPONENT_ALPHA_THRESHOLD = 8
_COMPONENT_MIN_PIXELS = 64
_COMPONENT_RELATIVE_MIN = 0.0005
_COMPONENT_LIMIT = 64


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


def canonicalize_components(matte_bytes: bytes, selected_component_ids: list[str]) -> dict:
    started = time.perf_counter()
    with Image.open(io.BytesIO(matte_bytes)) as opened:
        rgba = opened.convert("RGBA")
    analysis = analyze_components(rgba)
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
        alpha, labels, _ = _label_components(rgba.getchannel("A"))
        selected_labels = [int(available[item]["label"]) for item in selected]
        keep = np.isin(labels, np.asarray(selected_labels, dtype=np.int32))
        filtered_alpha = np.where(keep, alpha, 0).astype(np.uint8)
        filtered = rgba.copy()
        filtered.putalpha(Image.fromarray(filtered_alpha, mode="L"))

    bbox = _foreground_bbox(filtered.getchannel("A"))
    canonical = _letterbox_rgba(filtered, bbox)
    canonical_bytes = _png_bytes(canonical)
    visible = []
    for item in analysis["components"]:
        updated = dict(item)
        updated["selected"] = item["id"] in selected
        updated.pop("label", None)
        visible.append(updated)
    return {
        "canonical_bytes": canonical_bytes,
        "canonical_sha256": hashlib.sha256(canonical_bytes).hexdigest(),
        "source_size": analysis["source_size"],
        "foreground_bbox": list(bbox),
        "selected_component_ids": selected,
        "components": visible,
        "component_count": analysis["component_count"],
        "raw_component_count": analysis["raw_component_count"],
        "ignored_component_count": analysis["ignored_component_count"],
        "ignored_foreground_pixels": analysis["ignored_foreground_pixels"],
        "minimum_component_pixels": analysis["minimum_component_pixels"],
        "selection_elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
    }


def rembg_home() -> Path:
    path = data_dir() / "rembg"
    path.mkdir(parents=True, exist_ok=True)
    return path


def model_path() -> Path:
    return rembg_home() / "models" / ENGINE / f"{ENGINE}.onnx"


def status() -> dict:
    model = model_path()
    return {
        "engine": ENGINE,
        "provider": "cpu",
        "model_home": str(rembg_home()),
        "model_path": str(model),
        "model_downloaded": model.is_file() and model.stat().st_size > 0,
        "canonical_size": CANONICAL_SIZE,
        "cpu_threads": _available_cpu_threads(),
        "local_only": True,
    }


def _available_cpu_threads() -> int:
    try:
        available = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        available = os.cpu_count() or 4
    return max(1, min(8, available))


def _get_session():
    global _session
    with _session_lock:
        if _session is None:
            # Keep the ~1 GB model inside modal-3D-client app-data, not ~/.u2net.
            os.environ.setdefault("U2NET_HOME", str(rembg_home()))
            import onnxruntime as ort
            from rembg.session_factory import new_session

            options = ort.SessionOptions()
            options.intra_op_num_threads = _available_cpu_threads()
            options.inter_op_num_threads = 1
            _session = new_session(
                ENGINE,
                sess_opts=options,
                providers=["CPUExecutionProvider"],
            )
        return _session


def _predict_mask(image: Image.Image) -> Image.Image:
    masks = _get_session().predict(image.convert("RGB"))
    if not masks:
        raise RuntimeError("rembg 未返回前景 Alpha 掩码")
    return masks[0].convert("L")


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


def _png_bytes(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG", compress_level=6)
    return output.getvalue()


def process(data: bytes) -> dict:
    """Run global local matting and return full matte + canonical PNG bytes."""
    started = time.perf_counter()
    with Image.open(io.BytesIO(data)) as opened:
        source = ImageOps.exif_transpose(opened).convert("RGB")
    mask = _predict_mask(source)
    if mask.size != source.size:
        mask = mask.resize(source.size, Image.Resampling.LANCZOS)

    rgba = source.convert("RGBA")
    rgba.putalpha(mask)
    bbox = _foreground_bbox(mask)
    canonical = _letterbox_rgba(rgba, bbox)
    matte_bytes = _png_bytes(rgba)
    canonical_bytes = _png_bytes(canonical)
    component_analysis = analyze_components(rgba)

    histogram = mask.histogram()
    foreground_pixels = sum(histogram[_BBOX_ALPHA_THRESHOLD + 1 :])
    total_pixels = source.width * source.height
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    return {
        "matte_bytes": matte_bytes,
        "canonical_bytes": canonical_bytes,
        "matte_sha256": hashlib.sha256(matte_bytes).hexdigest(),
        "canonical_sha256": hashlib.sha256(canonical_bytes).hexdigest(),
        "source_size": [source.width, source.height],
        "foreground_bbox": list(bbox),
        "foreground_ratio": foreground_pixels / total_pixels if total_pixels else 0.0,
        "canonical_size": [CANONICAL_SIZE, CANONICAL_SIZE],
        "components": [
            {key: value for key, value in item.items() if key != "label"}
            for item in component_analysis["components"]
        ],
        "component_count": component_analysis["component_count"],
        "raw_component_count": component_analysis["raw_component_count"],
        "ignored_component_count": component_analysis["ignored_component_count"],
        "ignored_foreground_pixels": component_analysis["ignored_foreground_pixels"],
        "minimum_component_pixels": component_analysis["minimum_component_pixels"],
        "selected_component_ids": [item["id"] for item in component_analysis["components"]],
        "engine": ENGINE,
        "provider": "cpu",
        "elapsed_ms": elapsed_ms,
    }
