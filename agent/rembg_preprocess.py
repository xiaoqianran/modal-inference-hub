"""Local-only image matting and canonicalization for modal-3D.

The source image never leaves the machine during preprocessing. The active V1
pipeline is intentionally small: rembg/birefnet-general-lite produces one global
alpha matte, then the foreground bounding box is letterboxed to the cloud
contract (1024x1024, 8-bit RGBA PNG).
"""

from __future__ import annotations

import gc
import hashlib
import importlib
from collections import OrderedDict
import io
import json
import os
import threading
import time
import urllib.error
import urllib.request

import numpy as np
from scipy import ndimage
from pathlib import Path

from PIL import Image, ImageOps

from agent.storage import data_dir

ENGINE = "birefnet-general-lite"
MODEL_URL = "https://github.com/danielgatis/rembg/releases/download/v0.0.0/BiRefNet-general-bb_swin_v1_tiny-epoch_232.onnx"
MODEL_MD5 = "4fab47adc4ff364be1713e97b7e66334"
MODEL_BYTES = 224_005_088
CANONICAL_SIZE = 1024
_BBOX_ALPHA_THRESHOLD = 8
_session_lock = threading.RLock()
_inference_lock = threading.Lock()
_download_lock = threading.Lock()
_download_state_lock = threading.RLock()
_prepare_thread_lock = threading.RLock()
_prepare_thread: threading.Thread | None = None
_download_state = {
    "status": "idle",
    "downloaded_bytes": 0,
    "total_bytes": MODEL_BYTES,
    "error": None,
    "integrity": "unverified",
}
_verified_model_signature: tuple[int, int] | None = None
_session = None
_session_provider: str | None = None
_session_ort_provider: str | None = None
_session_fallback_reason: str | None = None
_session_release_timer: threading.Timer | None = None
_CUDA_SESSION_IDLE_SECONDS = 60.0
_cuda_runtime_lock = threading.Lock()
_cuda_runtime_loaded = False
_cuda_dll_directory_handles: list[object] = []
_CUDA_PACKAGE_MODULES = (
    "cublas",
    "cuda_runtime",
    "cudnn",
    "cufft",
)
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
    selection_bytes = _png_bytes(filtered, compress_level=1)
    canonical_bytes = _png_bytes(canonical, compress_level=1)
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
        return "gpu"
    provider = value.get("provider") if isinstance(value, dict) else None
    return provider if provider in {"cpu", "gpu"} else "gpu"


def _available_ort_providers() -> list[str]:
    try:
        import onnxruntime as ort

        return list(ort.get_available_providers())
    except Exception:
        return []


def available_providers() -> list[str]:
    providers = _available_ort_providers()
    result = ["cpu"] if "CPUExecutionProvider" in providers else []
    if "CUDAExecutionProvider" in providers:
        result.append("gpu")
    return result


def reset_session() -> None:
    global _session, _session_provider, _session_ort_provider, _session_fallback_reason
    global _session_release_timer
    with _session_lock:
        timer = _session_release_timer
        _session_release_timer = None
        session = _session
        _session = None
        _session_provider = None
        _session_ort_provider = None
        _session_fallback_reason = None
    if timer is not None:
        timer.cancel()
    # ONNX Runtime GPU providers own large native allocations.  Drop the
    # last Python reference and collect outside the lock so switching providers
    # releases those allocations promptly instead of waiting for a later GC.
    del session
    gc.collect()


def _release_session_resources() -> None:
    """Release model/native allocations while preserving last-run diagnostics."""
    global _session, _session_release_timer
    with _session_lock:
        timer = _session_release_timer
        _session_release_timer = None
        session = _session
        _session = None
    if timer is not None:
        timer.cancel()
    del session
    gc.collect()


def _schedule_session_release() -> None:
    """Keep a CUDA session briefly warm, then release its resident weights."""
    global _session_release_timer
    with _session_lock:
        if _session_release_timer is not None:
            _session_release_timer.cancel()
        timer = threading.Timer(_CUDA_SESSION_IDLE_SECONDS, _release_session_resources)
        timer.name = "modal-3d-rembg-session-release"
        timer.daemon = True
        _session_release_timer = timer
        timer.start()


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


def partial_model_path() -> Path:
    return model_path().with_suffix(".onnx.partial")


def _set_download_state(**changes) -> None:
    with _download_state_lock:
        _download_state.update(changes)


def _download_status() -> dict:
    model = model_path()
    partial = partial_model_path()
    with _download_state_lock:
        state = dict(_download_state)
    if state["status"] not in {"downloading", "verifying"}:
        if model.is_file():
            state["downloaded_bytes"] = model.stat().st_size
            if state["integrity"] == "verified":
                state["status"] = "ready"
        elif partial.is_file():
            state["downloaded_bytes"] = partial.stat().st_size
            if state["status"] != "failed":
                state["status"] = "idle"
        else:
            state["downloaded_bytes"] = 0
            if state["status"] != "failed":
                state["status"] = "idle"
    total = int(state.get("total_bytes") or MODEL_BYTES)
    downloaded = int(state.get("downloaded_bytes") or 0)
    state["total_bytes"] = total
    state["progress"] = min(1.0, downloaded / total) if total > 0 else 0.0
    state["resumable"] = partial.is_file() and partial.stat().st_size > 0
    return state


def _md5(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_model(path: Path) -> None:
    global _verified_model_signature
    stat = path.stat()
    signature = (stat.st_mtime_ns, stat.st_size)
    if _verified_model_signature == signature:
        _set_download_state(status="ready", integrity="verified", downloaded_bytes=stat.st_size, error=None)
        return
    _set_download_state(status="verifying", integrity="verifying", downloaded_bytes=stat.st_size, error=None)
    if stat.st_size != MODEL_BYTES:
        raise RuntimeError(f"模型文件大小异常：{stat.st_size} / {MODEL_BYTES} bytes")
    actual = _md5(path)
    if actual != MODEL_MD5:
        raise RuntimeError(f"{ENGINE} 模型 MD5 校验失败")
    _verified_model_signature = signature
    _set_download_state(status="ready", integrity="verified", downloaded_bytes=stat.st_size, error=None)


def _download_model() -> Path:
    model = model_path()
    partial = partial_model_path()
    model.parent.mkdir(parents=True, exist_ok=True)
    existing = partial.stat().st_size if partial.is_file() else 0
    if existing > MODEL_BYTES:
        partial.unlink(missing_ok=True)
        existing = 0
    elif existing == MODEL_BYTES:
        _set_download_state(
            status="verifying",
            downloaded_bytes=existing,
            total_bytes=MODEL_BYTES,
            error=None,
            integrity="verifying",
        )
        if _md5(partial) == MODEL_MD5:
            partial.replace(model)
            _verify_model(model)
            return model
        partial.unlink(missing_ok=True)
        existing = 0
        _set_download_state(
            status="failed",
            downloaded_bytes=0,
            error="MD5 校验失败；损坏的 partial 已删除",
            integrity="failed",
        )
    headers = {"User-Agent": "modal-3D-client/1"}
    if existing:
        headers["Range"] = f"bytes={existing}-"
    request = urllib.request.Request(MODEL_URL, headers=headers)
    _set_download_state(
        status="downloading",
        downloaded_bytes=existing,
        total_bytes=MODEL_BYTES,
        error=None,
        integrity="unverified",
    )
    try:
        response = urllib.request.urlopen(request, timeout=30)
        status_code = getattr(response, "status", response.getcode())
        append = existing > 0 and status_code == 206
        if existing > 0 and not append:
            existing = 0
            _set_download_state(downloaded_bytes=0)
        mode = "ab" if append else "wb"
        downloaded = existing
        with response, partial.open(mode) as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                downloaded += len(chunk)
                _set_download_state(downloaded_bytes=downloaded)
    except Exception as exc:
        downloaded = partial.stat().st_size if partial.is_file() else 0
        _set_download_state(
            status="failed",
            downloaded_bytes=downloaded,
            error=f"{type(exc).__name__}: {exc}",
            integrity="unverified",
        )
        raise RuntimeError(f"{ENGINE} 模型下载失败，可继续重试：{exc}") from exc

    if not partial.is_file() or partial.stat().st_size != MODEL_BYTES:
        downloaded = partial.stat().st_size if partial.is_file() else 0
        _set_download_state(
            status="failed",
            downloaded_bytes=downloaded,
            error=f"下载不完整：{downloaded} / {MODEL_BYTES} bytes",
            integrity="unverified",
        )
        raise RuntimeError(f"{ENGINE} 模型下载不完整，可继续重试")

    _set_download_state(status="verifying", downloaded_bytes=MODEL_BYTES, integrity="verifying", error=None)
    try:
        if _md5(partial) != MODEL_MD5:
            partial.unlink(missing_ok=True)
            _set_download_state(
                status="failed",
                downloaded_bytes=0,
                error="MD5 校验失败；损坏的 partial 已删除",
                integrity="failed",
            )
            raise RuntimeError(f"{ENGINE} 模型 MD5 校验失败，已删除损坏下载")
        partial.replace(model)
        _verify_model(model)
    except Exception:
        if partial.exists() and partial.stat().st_size == MODEL_BYTES:
            partial.unlink(missing_ok=True)
        raise
    return model


def ensure_model_ready() -> Path:
    model = model_path()
    with _download_lock:
        if model.is_file():
            try:
                _verify_model(model)
                return model
            except RuntimeError:
                model.unlink(missing_ok=True)
                _set_download_state(status="failed", downloaded_bytes=0, integrity="failed")
        return _download_model()


def prepare_model_async() -> dict:
    global _prepare_thread
    with _prepare_thread_lock:
        if model_path().is_file() and _download_status()["integrity"] == "verified":
            return status()
        if _prepare_thread is not None and _prepare_thread.is_alive():
            return status()

        def worker() -> None:
            try:
                ensure_model_ready()
            except Exception:
                # The detailed failure is already persisted in _download_state for UI polling.
                pass

        _prepare_thread = threading.Thread(
            target=worker,
            name="modal-3d-rembg-model-prepare",
            daemon=True,
        )
        _prepare_thread.start()
        return status()


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
        "model_downloaded": model.is_file() and model.stat().st_size == MODEL_BYTES,
        "model_bytes": MODEL_BYTES,
        "download": _download_status(),
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


def _session_options(ort):
    options = ort.SessionOptions()
    options.intra_op_num_threads = _available_cpu_threads()
    options.inter_op_num_threads = 1
    # CPU fallback should return its arena allocations to Windows after the
    # one-shot preprocessing request rather than keeping the RAM high-water
    # mark for the lifetime of the desktop app.
    options.enable_cpu_mem_arena = False
    return options


def _preload_cuda_runtime(ort) -> None:
    """Load the CUDA/cuDNN DLLs shipped by the ONNX Runtime Python extras."""
    global _cuda_runtime_loaded
    with _cuda_runtime_lock:
        if _cuda_runtime_loaded:
            return

        bin_directories: list[str] = []
        for package_name in _CUDA_PACKAGE_MODULES:
            package = importlib.import_module(f"nvidia.{package_name}")
            for package_root in package.__path__:
                binary_directory = Path(package_root) / "bin"
                if binary_directory.is_dir():
                    resolved = str(binary_directory.resolve())
                    bin_directories.append(resolved)
                    if os.name == "nt":
                        _cuda_dll_directory_handles.append(os.add_dll_directory(resolved))

        if not bin_directories:
            raise RuntimeError("没有找到随应用安装的 NVIDIA CUDA/cuDNN DLL")

        # cuDNN 9 loads split sub-libraries by filename at inference time.  Keep
        # their directories in PATH as well as the Windows DLL directory list.
        current_path = os.environ.get("PATH", "")
        current_entries = {entry.casefold() for entry in current_path.split(os.pathsep) if entry}
        missing = [entry for entry in bin_directories if entry.casefold() not in current_entries]
        if missing:
            os.environ["PATH"] = os.pathsep.join([*missing, current_path])

        preload = getattr(ort, "preload_dlls", None)
        if preload is not None:
            # An empty directory tells ORT to load from NVIDIA's Python packages.
            preload(directory="")
        _cuda_runtime_loaded = True


def _new_cpu_session():
    import onnxruntime as ort
    from rembg.session_factory import new_session

    return new_session(
        ENGINE,
        sess_opts=_session_options(ort),
        providers=["CPUExecutionProvider"],
    )


def _get_session():
    global _session, _session_provider, _session_ort_provider, _session_fallback_reason
    global _session_release_timer
    with _session_lock:
        if _session_release_timer is not None:
            _session_release_timer.cancel()
            _session_release_timer = None
        if _session is not None:
            return _session

        os.environ.setdefault("U2NET_HOME", str(rembg_home()))
        ensure_model_ready()
        import onnxruntime as ort
        from rembg.session_factory import new_session

        preference = provider_preference()
        available = ort.get_available_providers()
        requested = ["CPUExecutionProvider"]
        try:
            if preference == "gpu":
                if "CUDAExecutionProvider" in available:
                    _preload_cuda_runtime(ort)
                    requested = [
                        (
                            "CUDAExecutionProvider",
                            {
                                "device_id": 0,
                                "do_copy_in_default_stream": 1,
                                "cudnn_conv_algo_search": "HEURISTIC",
                                "cudnn_conv_use_max_workspace": 0,
                                "arena_extend_strategy": "kSameAsRequested",
                                "use_tf32": 1,
                            },
                        ),
                        "CPUExecutionProvider",
                    ]
                else:
                    _session_fallback_reason = "没有可用的 CUDA ExecutionProvider，已回退 CPU"
            options = _session_options(ort)
            _session = new_session(ENGINE, sess_opts=options, providers=requested)
            actual = list(_session.inner_session.get_providers())
            _session_ort_provider = actual[0] if actual else None
            _session_provider = "gpu" if "CUDAExecutionProvider" in actual else "cpu"
            if preference == "gpu" and _session_provider != "gpu":
                _session_fallback_reason = "CUDA provider 初始化后未激活，已回退 CPU"
        except Exception as exc:
            if preference != "gpu":
                raise
            _session_fallback_reason = f"CUDA 初始化失败，已回退 CPU: {type(exc).__name__}"
            _session = _new_cpu_session()
            _session_provider = "cpu"
            _session_ort_provider = "CPUExecutionProvider"
        return _session


def _predict_with_cuda_session(session, image: Image.Image) -> Image.Image:
    """Run BiRefNet while returning CUDA arena scratch memory after each image."""
    import onnxruntime as ort

    run_options = ort.RunOptions()
    run_options.add_run_config_entry("memory.enable_memory_arena_shrinkage", "gpu:0")
    outputs = session.inner_session.run(
        None,
        session.normalize(
            image,
            (0.485, 0.456, 0.406),
            (0.229, 0.224, 0.225),
            (CANONICAL_SIZE, CANONICAL_SIZE),
        ),
        run_options,
    )
    prediction = session.sigmoid(outputs[0][:, 0, :, :])
    maximum = np.max(prediction)
    minimum = np.min(prediction)
    if maximum > minimum:
        prediction = (prediction - minimum) / (maximum - minimum)
    prediction = np.squeeze(prediction)
    mask = Image.fromarray((prediction * 255).astype("uint8"), mode="L")
    return mask.resize(image.size, Image.Resampling.LANCZOS)


def _predict_mask(image: Image.Image) -> Image.Image:
    global _session, _session_provider, _session_ort_provider, _session_fallback_reason
    session = _get_session()
    provider = _session_provider
    gpu_failure: str | None = None
    with _inference_lock:
        try:
            rgb = image.convert("RGB")
            if _session_ort_provider == "CUDAExecutionProvider":
                masks = [_predict_with_cuda_session(session, rgb)]
            else:
                masks = session.predict(rgb)
        except Exception as exc:
            if provider != "gpu":
                if isinstance(exc, UnicodeDecodeError):
                    raise RuntimeError(
                        "本地推理失败，底层运行时返回了无法解码的 Windows 错误"
                    ) from exc
                raise
            if isinstance(exc, UnicodeDecodeError):
                gpu_failure = "CUDA 原生错误无法解码，通常是显存不足或驱动执行失败"
            else:
                gpu_failure = f"CUDA 执行失败（{type(exc).__name__}）"

        if gpu_failure is not None:
            # A failed CUDA run can retain a large native GPU allocation.
            # Destroy it before constructing the CPU session, otherwise the
            # fallback itself may run the machine out of RAM.
            session = None
            _release_session_resources()
            try:
                session = _new_cpu_session()
                _session = session
                _session_provider = "cpu"
                _session_ort_provider = "CPUExecutionProvider"
                _session_fallback_reason = f"{gpu_failure}，已释放 GPU 并回退 CPU"
                masks = session.predict(image.convert("RGB"))
            except Exception as exc:
                _release_session_resources()
                raise RuntimeError(
                    "CUDA 推理失败，CPU 回退也未能完成；"
                    "请关闭占用内存的程序，或在设置中改用 CPU 后重试"
                ) from exc
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
    try:
        mask = _predict_mask(source)
        active_provider = _session_provider or provider_preference()
        fallback_reason = _session_fallback_reason
    finally:
        if _session_ort_provider == "CUDAExecutionProvider":
            # CUDA scratch arenas are shrunk after every run, so retaining the
            # weights briefly gives sub-second warm inference without pinning the
            # full peak allocation. Release the session after an idle minute.
            _schedule_session_release()
        else:
            _release_session_resources()
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
        "provider": active_provider,
        "provider_preference": provider_preference(),
        "fallback_reason": fallback_reason,
        "elapsed_ms": elapsed_ms,
    }
