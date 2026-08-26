"""Stable facade for rembg preprocessing (local or cloud).

The facade keeps the existing local ONNX Runtime provider semantics untouched
(`provider_preference()` / `set_provider_preference()` still select `cpu` vs
`gpu` for the LOCAL path, and their callers and tests are unchanged).

On top of that it adds an orthogonal *execution location*:

- `execution_preference()` returns the stored choice: `auto` (default),
  `cloud`, or `local`.
- `resolved_execution()` maps `auto` to `local` when an NVIDIA GPU
  (ONNXRuntime CUDAExecutionProvider) is available, otherwise to `cloud`.
- `process(data)` follows `resolved_execution()`: the cloud `modal-3d-rembg`
  T4 endpoint, or the local ONNX Runtime path.

So the default is fully automatic: a machine with a suitable NVIDIA GPU runs
locally and offline; anything else falls back to the cloud with no local GPU
or model download required. The user can still force cloud or local manually.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

import modal
from PIL import Image, ImageOps

from agent import modal_client
from agent.preprocess import image_ops, model_store, runtime
from agent.storage import data_dir

CLOUD_APP = "modal-3d-rembg"
CLOUD_FUNCTION = "web"
CLOUD_TIMEOUT_SECONDS = 300

# Existing local-provider surface (cpu/gpu) is preserved for callers and tests.
analyze_components = image_ops.analyze_components
canonicalize_components = image_ops.canonicalize_components
clear_selection_cache = image_ops.clear_selection_cache
provider_preference = runtime.provider_preference
available_providers = runtime.available_providers
set_provider_preference = runtime.set_provider_preference
reset_session = runtime.reset_session
warmup_gpu_async = runtime.warmup_gpu_async

_EXECUTION_VALUES = {"auto", "cloud", "local"}


def _execution_settings_path() -> Path:
    return data_dir() / "rembg" / "execution.json"


def execution_preference() -> str:
    try:
        payload = json.loads(_execution_settings_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return "auto"
    value = payload.get("execution") if isinstance(payload, dict) else None
    # Legacy "cloud"/"local" values remain valid; anything unknown resets to auto.
    return value if value in _EXECUTION_VALUES else "auto"


def resolved_execution() -> str:
    """Map the preference to a concrete location.

    `auto` runs locally when a suitable NVIDIA GPU is present, else in the
    cloud. An explicit `cloud`/`local` choice is honoured as-is.
    """
    preference = execution_preference()
    if preference != "auto":
        return preference
    return "local" if "gpu" in available_providers() else "cloud"


def set_execution_preference(value: str) -> dict:
    if value not in _EXECUTION_VALUES:
        raise ValueError("execution 必须是 auto、cloud 或 local")
    path = _execution_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps({"execution": value}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)
    return status()


def prepare_model_async() -> dict:
    model_store.prepare_model_async()
    return status()


def status() -> dict:
    result = runtime.status()
    result["execution"] = execution_preference()
    result["resolved_execution"] = resolved_execution()
    result["cloud_connected"] = modal_client.connected()
    return result


def _cloud_url() -> str:
    try:
        client = modal_client.client()
    except modal_client.NotConnectedError as exc:
        raise RuntimeError("Modal 尚未连接，无法使用云端 rembg") from exc
    fn = modal.Function.from_name(CLOUD_APP, CLOUD_FUNCTION, client=client)
    return fn.get_web_url()


def _build_result(rgba, mask, *, provider, execution, fallback_reason, started, engine) -> dict:
    """Shared post-processing: mask+rgba -> matte/canonical bytes + component analysis.

    Both the local and cloud paths converge here so the canonical letterbox and
    component analysis run exactly once, in exactly one place.
    """
    bbox = image_ops._foreground_bbox(mask)
    canonical = image_ops._letterbox_rgba(rgba, bbox)
    matte_bytes = image_ops._png_bytes(rgba)
    canonical_bytes = image_ops._png_bytes(canonical)
    analysis = image_ops.analyze_components(rgba)
    histogram = mask.histogram()
    foreground_pixels = sum(histogram[9:])
    total_pixels = rgba.width * rgba.height
    return {
        "matte_bytes": matte_bytes,
        "canonical_bytes": canonical_bytes,
        "matte_sha256": hashlib.sha256(matte_bytes).hexdigest(),
        "canonical_sha256": hashlib.sha256(canonical_bytes).hexdigest(),
        "source_size": [rgba.width, rgba.height],
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
        "engine": engine,
        "provider": provider,
        "provider_preference": provider if execution == "cloud" else provider_preference(),
        "execution": execution,
        "fallback_reason": fallback_reason,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
    }


def _cloud_process(data: bytes) -> dict:
    url = f"{_cloud_url()}/preprocess"
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/octet-stream"},
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=CLOUD_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail", "")
        except Exception:  # noqa: BLE001 - fall through to generic message
            pass
        raise RuntimeError(f"云端 rembg 失败 ({exc.code}): {detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"云端 rembg 不可达: {exc.reason}") from exc

    try:
        mask_bytes = base64.b64decode(payload["mask_bytes_b64"])
    except (KeyError, ValueError) as exc:
        raise RuntimeError("云端 rembg 返回了无效的响应") from exc

    with Image.open(io.BytesIO(mask_bytes)) as opened:
        mask = opened.convert("L")
    source = _load_source(data)
    return _finish(source, mask, provider="cloud", execution="cloud", fallback_reason=None, started=started, engine=payload.get("engine", model_store.ENGINE))


def _load_source(data: bytes):
    with Image.open(io.BytesIO(data)) as opened:
        return ImageOps.exif_transpose(opened).convert("RGB")


def _finish(source, mask, *, provider, execution, fallback_reason, started, engine) -> dict:
    """Shared tail: source RGB + alpha mask -> full result.

    Both the local and cloud paths converge here, so the mask repair and the
    canonical letterbox / component analysis run exactly once, in one place.
    """
    if mask.size != source.size:
        mask = mask.resize(source.size, Image.Resampling.LANCZOS)
    # Repair the raw mask so genuine foreground is not carved out: undo the
    # over-aggressive min-max normalisation effect and heal holes the model
    # poked inside the subject. Applied identically to local and cloud masks.
    mask = image_ops.refine_mask(mask)
    rgba = source.convert("RGBA")
    rgba.putalpha(mask)
    return _build_result(
        rgba, mask,
        provider=provider, execution=execution, fallback_reason=fallback_reason,
        started=started, engine=engine,
    )


def process(data: bytes) -> dict:
    started = time.perf_counter()
    if resolved_execution() == "cloud":
        return _cloud_process(data)

    source = _load_source(data)
    mask, active_provider, fallback_reason = runtime.predict_mask(source)
    return _finish(
        source, mask,
        provider=active_provider, execution="local", fallback_reason=fallback_reason,
        started=started, engine=model_store.ENGINE,
    )
