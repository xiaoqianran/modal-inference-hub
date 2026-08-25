"""Local-only image matting and canonicalization for modal-3D.

The source image never leaves the machine during preprocessing. The active V1
pipeline is intentionally small: rembg/birefnet-general produces one global
alpha matte, then the foreground bounding box is letterboxed to the cloud
contract (1024x1024, 8-bit RGBA PNG).
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
import io
import json
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
_inference_lock = threading.Lock()
_session = None
_session_provider: str | None = None
_session_fallback_reason: str | None = None
_selection_cache_lock = threading.RLock()
_selection_cache: OrderedDict[str, tuple[Image.Image, np.ndarray, int]] = OrderedDict()
_selection_cache_bytes = 0
_SELECTION_CACHE_LIMIT = 4
_SELECTION_CACHE_MAX_BYTES = 64 * 1024 * 1024

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
    canonical_bytes = _png_bytes(canonical, compress_level=1)
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
        "raw_component_count": analysis.get("raw_component_count", analysis["component_count"]),
        "ignored_component_count": analysis.get("ignored_component_count", 0),
        "ignored_foreground_pixels": analysis.get("ignored_foreground_pixels", 0),
        "minimum_component_pixels": analysis.get("minimum_component_pixels", _COMPONENT_MIN_PIXELS),
        "selection_elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
    }


def rembg_home() -> Path:
    path = data_dir() / "rembg"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _provider_settings_path() -> Path:
    return rembg_home() / "provider.json"


def provider_preference() -> str:
    path = _provider_settings_path()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return "cpu"
    provider = value.get("provider") if isinstance(value, dict) else None
    return provider if provider in {"cpu", "gpu"} else "cpu"


def _available_ort_providers() -> list[str]:
    try:
        import onnxruntime as ort

        return list(ort.get_available_providers())
    except Exception:
        return []


def available_providers() -> list[str]:
    providers = _available_ort_providers()
    result = ["cpu"] if "CPUExecutionProvider" in providers else []
    if "DmlExecutionProvider" in providers or "CUDAExecutionProvider" in providers:
        result.append("gpu")
    return result


def reset_session() -> None:
    global _session, _session_provider, _session_fallback_reason
    with _session_lock:
        _session = None
        _session_provider = None
        _session_fallback_reason = None


def set_provider_preference(provider: str) -> dict:
    if provider not in {"cpu", "gpu"}:
        raise ValueError("provider 必须是 cpu 或 gpu")
    path = _provider_settings_path()
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps({"provider": provider}, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    reset_session()
    return status()


def model_path() -> Path:
    return rembg_home() / "models" / ENGINE / f"{ENGINE}.onnx"


def status() -> dict:
    model = model_path()
    preference = provider_preference()
    providers = available_providers()
    active = _session_provider
    fallback = _session_fallback_reason
    if active is None:
        if preference == "gpu" and "gpu" not in providers:
            active = "cpu"
            fallback = "没有可用的 GPU ExecutionProvider，将回退 CPU"
        else:
            active = preference
    return {
        "engine": ENGINE,
        "provider": active,
        "provider_preference": preference,
        "available_providers": providers,
        "ort_providers": _available_ort_providers(),
        "gpu_available": "gpu" in providers,
        "fallback_reason": fallback,
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
    global _session, _session_provider, _session_fallback_reason
    with _session_lock:
        if _session is not None:
            return _session

        os.environ.setdefault("U2NET_HOME", str(rembg_home()))
        import onnxruntime as ort
        from rembg.session_factory import new_session

        options = ort.SessionOptions()
        options.intra_op_num_threads = _available_cpu_threads()
        options.inter_op_num_threads = 1
        preference = provider_preference()
        available = ort.get_available_providers()
        requested = ["CPUExecutionProvider"]
        if preference == "gpu":
            if "DmlExecutionProvider" in available:
                requested = ["DmlExecutionProvider", "CPUExecutionProvider"]
                options.enable_mem_pattern = False
                options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            elif "CUDAExecutionProvider" in available:
                requested = ["CUDAExecutionProvider", "CPUExecutionProvider"]
            else:
                _session_fallback_reason = "没有可用的 GPU ExecutionProvider，已回退 CPU"

        try:
            _session = new_session(ENGINE, sess_opts=options, providers=requested)
            actual = list(_session.inner_session.get_providers())
            _session_provider = "gpu" if any(
                provider in actual for provider in ("DmlExecutionProvider", "CUDAExecutionProvider")
            ) else "cpu"
            if preference == "gpu" and _session_provider != "gpu":
                _session_fallback_reason = "GPU provider 初始化后未激活，已回退 CPU"
        except Exception as exc:
            if preference != "gpu":
                raise
            _session_fallback_reason = f"GPU 初始化失败，已回退 CPU: {type(exc).__name__}"
            _session = new_session(
                ENGINE,
                sess_opts=options,
                providers=["CPUExecutionProvider"],
            )
            _session_provider = "cpu"
        return _session


def _predict_mask(image: Image.Image) -> Image.Image:
    session = _get_session()
    with _inference_lock:
        masks = session.predict(image.convert("RGB"))
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


def _png_bytes(image: Image.Image, compress_level: int = 6) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG", compress_level=compress_level)
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
        "provider": _session_provider or "cpu",
        "provider_preference": provider_preference(),
        "fallback_reason": _session_fallback_reason,
        "elapsed_ms": elapsed_ms,
    }
