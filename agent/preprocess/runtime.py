"""ONNX Runtime provider/session lifecycle and GPU-to-CPU fallback."""
from __future__ import annotations

import gc
import importlib
import json
import os
import threading
from pathlib import Path
import numpy as np
from PIL import Image
from . import model_store
from .image_ops import CANONICAL_SIZE

ENGINE = model_store.ENGINE
_session_lock = threading.RLock()
_inference_lock = threading.Lock()
_session = None
_session_provider: str | None = None
_session_ort_provider: str | None = None
_session_fallback_reason: str | None = None
_session_release_timer: threading.Timer | None = None
_CUDA_SESSION_IDLE_SECONDS = 60.0
_cuda_runtime_lock = threading.Lock()
_cuda_runtime_loaded = False
_cuda_dll_directory_handles: list[object] = []
_CUDA_PACKAGE_MODULES = ("cublas", "cuda_runtime", "cudnn", "cufft")

def _provider_settings_path() -> Path:
    return model_store.rembg_home() / "provider.json"


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


def status() -> dict:
    model = model_store.model_path()
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
        "model_home": str(model_store.rembg_home()),
        "model_path": str(model),
        "model_downloaded": model.is_file() and model.stat().st_size == model_store.MODEL_BYTES,
        "model_bytes": model_store.MODEL_BYTES,
        "download": model_store.download_status(),
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

        os.environ.setdefault("U2NET_HOME", str(model_store.rembg_home()))
        model_store.ensure_model_ready()
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


def predict_mask(image: Image.Image) -> tuple[Image.Image, str, str | None]:
    """执行一次推理，并返回实际 provider 与 fallback 诊断。"""
    try:
        mask = _predict_mask(image)
        provider = _session_provider or provider_preference()
        return mask, provider, _session_fallback_reason
    finally:
        if _session_ort_provider == "CUDAExecutionProvider":
            _schedule_session_release()
        else:
            _release_session_resources()
