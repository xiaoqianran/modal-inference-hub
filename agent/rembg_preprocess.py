"""Stable facade for local rembg preprocessing."""
from __future__ import annotations

import hashlib
import io
import time
from PIL import Image, ImageOps
from agent.preprocess import image_ops, model_store, runtime

analyze_components = image_ops.analyze_components
canonicalize_components = image_ops.canonicalize_components
clear_selection_cache = image_ops.clear_selection_cache
provider_preference = runtime.provider_preference
available_providers = runtime.available_providers
set_provider_preference = runtime.set_provider_preference
reset_session = runtime.reset_session
warmup_gpu_async = runtime.warmup_gpu_async
def prepare_model_async() -> dict:
    model_store.prepare_model_async()
    return status()

def status() -> dict:
    return runtime.status()

def process(data: bytes) -> dict:
    started = time.perf_counter()
    with Image.open(io.BytesIO(data)) as opened:
        source = ImageOps.exif_transpose(opened).convert("RGB")
    mask, active_provider, fallback_reason = runtime.predict_mask(source)
    if mask.size != source.size:
        mask = mask.resize(source.size, Image.Resampling.LANCZOS)
    rgba = source.convert("RGBA")
    rgba.putalpha(mask)
    bbox = image_ops._foreground_bbox(mask)
    canonical = image_ops._letterbox_rgba(rgba, bbox)
    matte_bytes = image_ops._png_bytes(rgba)
    canonical_bytes = image_ops._png_bytes(canonical)
    analysis = image_ops.analyze_components(rgba)
    histogram = mask.histogram()
    foreground_pixels = sum(histogram[9:])
    total_pixels = source.width * source.height
    return {
        "matte_bytes": matte_bytes,
        "canonical_bytes": canonical_bytes,
        "matte_sha256": hashlib.sha256(matte_bytes).hexdigest(),
        "canonical_sha256": hashlib.sha256(canonical_bytes).hexdigest(),
        "source_size": [source.width, source.height],
        "foreground_bbox": list(bbox),
        "foreground_ratio": foreground_pixels / total_pixels if total_pixels else 0.0,
        "canonical_size": [image_ops.CANONICAL_SIZE, image_ops.CANONICAL_SIZE],
        "components": [{k: v for k, v in item.items() if k != "label"} for item in analysis["components"]],
        "component_count": analysis["component_count"],
        "raw_component_count": analysis["raw_component_count"],
        "ignored_component_count": analysis["ignored_component_count"],
        "ignored_foreground_pixels": analysis["ignored_foreground_pixels"],
        "minimum_component_pixels": analysis["minimum_component_pixels"],
        "selected_component_ids": [item["id"] for item in analysis["components"]],
        "engine": model_store.ENGINE,
        "provider": active_provider,
        "provider_preference": provider_preference(),
        "fallback_reason": fallback_reason,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
    }
