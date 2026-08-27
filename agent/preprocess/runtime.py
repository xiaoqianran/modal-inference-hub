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

from . import gpu_runtime, model_store
from .image_ops import CANONICAL_SIZE

ENGINE = model_store.ENGINE
_session_lock = threading.RLock()
_session_build_lock = threading.Lock()
_inference_lock = threading.Lock()
_session = None
_session_provider: str | None = None
_session_ort_provider: str | None = None
_session_fallback_reason: str | None = None
_warmup_lock = threading.Lock()
_warmup_thread: threading.Thread | None = None
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
    with _session_lock:
        session = _session
        _session = None
        _session_provider = None
        _session_ort_provider = None
        _session_fallback_reason = None
    # ONNX Runtime GPU providers own large native allocations.  Drop the
    # last Python reference and collect outside the lock so switching providers
    # releases those allocations promptly instead of waiting for a later GC.
    del session
    gc.collect()


def _release_session_resources() -> None:
    """Release model/native allocations while preserving last-run diagnostics."""
    global _session
    with _session_lock:
        session = _session
        _session = None
    del session
    gc.collect()


def set_provider_preference(provider: str) -> dict:
    if provider not in {"cpu", "gpu"}:
        raise ValueError("provider 必须是 cpu 或 gpu")
    path = _provider_settings_path()
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps({"provider": provider}, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    reset_session()
    if provider == "gpu":
        warmup_gpu_async()
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
        "gpu_warm": _session is not None and _session_provider == "gpu",
        "fallback_reason": fallback,
        "model_home": str(model_store.rembg_home()),
        "model_path": str(model),
        "model_downloaded": model.is_file() and model.stat().st_size == model_store.MODEL_BYTES,
        "model_bytes": model_store.MODEL_BYTES,
        "download": model_store.download_status(),
        "canonical_size": CANONICAL_SIZE,
        "cpu_threads": _available_cpu_threads(),
        "local_only": True,
        "gpu_runtime": gpu_runtime.status(),
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


def _register_cuda_directory(directory: Path) -> str:
    resolved = str(directory.resolve())
    if os.name == "nt":
        _cuda_dll_directory_handles.append(os.add_dll_directory(resolved))
    current_path = os.environ.get("PATH", "")
    entries = {entry.casefold() for entry in current_path.split(os.pathsep) if entry}
    if resolved.casefold() not in entries:
        os.environ["PATH"] = os.pathsep.join([resolved, current_path])
    return resolved


def _preload_cuda_runtime(ort, *, runtime_directory: Path | None = None, force: bool = False) -> None:
    """Expose CUDA/cuDNN without forcing the multi-GiB runtime into NSIS.

    Developer environments may have NVIDIA Python wheels already installed;
    end-user machines may instead have a compatible system runtime.  The
    verified on-demand runtime pack is passed explicitly on the retry path.
    """
    global _cuda_runtime_loaded
    with _cuda_runtime_lock:
        if _cuda_runtime_loaded and not force:
            return

        bin_directories: list[str] = []
        explicit_directory: Path | None = None
        if runtime_directory is not None and runtime_directory.is_dir():
            explicit_directory = runtime_directory
        elif gpu_runtime.ready():
            explicit_directory = gpu_runtime.bin_dir()

        if explicit_directory is not None:
            bin_directories.append(_register_cuda_directory(explicit_directory))
        else:
            # Dynamic imports are deliberate. The desktop PyInstaller build
            # explicitly excludes nvidia.*, while a developer venv can still
            # reuse the official CUDA wheels it already has.
            for package_name in _CUDA_PACKAGE_MODULES:
                try:
                    package = importlib.import_module(f"nvidia.{package_name}")
                except (ImportError, ModuleNotFoundError):
                    continue
                for package_root in package.__path__:
                    binary_directory = Path(package_root) / "bin"
                    if binary_directory.is_dir():
                        bin_directories.append(_register_cuda_directory(binary_directory))

        preload = getattr(ort, "preload_dlls", None)
        if preload is not None:
            if explicit_directory is not None:
                preload(cuda=True, cudnn=True, directory=str(explicit_directory))
            elif bin_directories:
                # Empty directory asks ORT to discover NVIDIA Python packages.
                preload(cuda=True, cudnn=True, directory="")
            else:
                # Last cheap attempt before downloading the runtime pack: use a
                # compatible system CUDA/cuDNN installation if one is present.
                preload(cuda=True, cudnn=True)
        _cuda_runtime_loaded = True


def _install_and_preload_cuda_runtime(ort) -> Path:
    directory = gpu_runtime.ensure_runtime()
    _preload_cuda_runtime(ort, runtime_directory=directory, force=True)
    return directory


class _BiRefNetSession:
    """Small BiRefNet-Lite ONNX adapter used by the desktop runtime.

    This intentionally mirrors only the normalization/prediction behavior we
    use from rembg.  The cloud deployment still installs rembg inside Modal,
    while the Windows sidecar no longer needs rembg's pymatting/numba/skimage
    dependency tree.
    """

    def __init__(self, inner_session) -> None:
        self.inner_session = inner_session

    def normalize(
        self,
        image: Image.Image,
        mean: tuple[float, float, float],
        std: tuple[float, float, float],
        size: tuple[int, int],
    ) -> dict[str, np.ndarray]:
        resized = image.convert("RGB").resize(size, Image.Resampling.LANCZOS)
        values = np.asarray(resized, dtype=np.float32)
        values /= max(float(np.max(values)), 1e-6)
        values = (values - np.asarray(mean, dtype=np.float32)) / np.asarray(std, dtype=np.float32)
        values = np.transpose(values, (2, 0, 1))[None, ...].astype(np.float32, copy=False)
        return {self.inner_session.get_inputs()[0].name: values}

    @staticmethod
    def sigmoid(values: np.ndarray) -> np.ndarray:
        return 1.0 / (1.0 + np.exp(-values))

    def predict(self, image: Image.Image) -> list[Image.Image]:
        outputs = self.inner_session.run(
            None,
            self.normalize(
                image,
                (0.485, 0.456, 0.406),
                (0.229, 0.224, 0.225),
                (CANONICAL_SIZE, CANONICAL_SIZE),
            ),
        )
        prediction = self.sigmoid(outputs[0][:, 0, :, :])
        maximum = float(np.max(prediction))
        minimum = float(np.min(prediction))
        if maximum > minimum:
            prediction = (prediction - minimum) / (maximum - minimum)
        prediction = np.squeeze(prediction)
        mask = Image.fromarray((prediction * 255).astype(np.uint8), mode="L")
        return [mask.resize(image.size, Image.Resampling.LANCZOS)]


def _new_birefnet_session(ort, *, sess_opts, providers):
    return _BiRefNetSession(
        ort.InferenceSession(
            str(model_store.model_path()),
            sess_options=sess_opts,
            providers=providers,
        )
    )


def _new_cpu_session():
    import onnxruntime as ort

    return _new_birefnet_session(
        ort,
        sess_opts=_session_options(ort),
        providers=["CPUExecutionProvider"],
    )


def _build_session(preference: str):
    import onnxruntime as ort

    available = ort.get_available_providers()
    requested = ["CPUExecutionProvider"]
    fallback: str | None = None

    def create_candidate():
        return _new_birefnet_session(
            ort,
            sess_opts=_session_options(ort),
            providers=requested,
        )

    try:
        if preference == "gpu":
            if "CUDAExecutionProvider" in available:
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
                try:
                    _preload_cuda_runtime(ort)
                except Exception:
                    if os.name != "nt":
                        raise
                    _install_and_preload_cuda_runtime(ort)
            else:
                fallback = "没有可用的 CUDA ExecutionProvider，已回退 CPU"

        try:
            candidate = create_candidate()
        except Exception:
            if (
                preference != "gpu"
                or os.name != "nt"
                or "CUDAExecutionProvider" not in available
                or gpu_runtime.ready()
            ):
                raise
            _install_and_preload_cuda_runtime(ort)
            candidate = create_candidate()

        actual = list(candidate.inner_session.get_providers())
        if (
            preference == "gpu"
            and os.name == "nt"
            and "CUDAExecutionProvider" in available
            and "CUDAExecutionProvider" not in actual
            and not gpu_runtime.ready()
        ):
            # ORT can silently fall back to CPU when a native CUDA DLL is
            # missing. Install the verified pack and give CUDA one real retry.
            del candidate
            gc.collect()
            _install_and_preload_cuda_runtime(ort)
            candidate = create_candidate()
            actual = list(candidate.inner_session.get_providers())

        ort_provider = actual[0] if actual else None
        provider = "gpu" if "CUDAExecutionProvider" in actual else "cpu"
        if preference == "gpu" and provider != "gpu":
            fallback = "CUDA provider 初始化后未激活，已回退 CPU"
        return candidate, provider, ort_provider, fallback
    except Exception as exc:
        if preference != "gpu":
            raise
        return (
            _new_cpu_session(),
            "cpu",
            "CPUExecutionProvider",
            f"CUDA 初始化失败，已回退 CPU: {type(exc).__name__}",
        )


def _get_session():
    global _session, _session_provider, _session_ort_provider, _session_fallback_reason
    with _session_lock:
        if _session is not None:
            return _session

    with _session_build_lock:
        with _session_lock:
            if _session is not None:
                return _session

        os.environ.setdefault("U2NET_HOME", str(model_store.rembg_home()))
        model_store.ensure_model_ready()

        while True:
            preference = provider_preference()
            candidate, provider, ort_provider, fallback = _build_session(preference)
            with _session_lock:
                if provider_preference() != preference:
                    existing = None
                elif _session is None:
                    _session = candidate
                    _session_provider = provider
                    _session_ort_provider = ort_provider
                    _session_fallback_reason = fallback
                    return _session
                else:
                    existing = _session
            del candidate
            gc.collect()
            if existing is not None:
                return existing


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
        if _session_ort_provider != "CUDAExecutionProvider":
            _release_session_resources()


def warmup_gpu_async() -> bool:
    """后台准备模型并常驻 GPU Session；CPU 偏好时绝不启动预热。"""
    global _warmup_thread
    if provider_preference() != "gpu":
        return False

    with _warmup_lock:
        if _session is not None and _session_provider == "gpu":
            return False
        if _warmup_thread is not None and _warmup_thread.is_alive():
            return False

        def worker() -> None:
            global _session_fallback_reason
            try:
                model_store.ensure_model_ready()
                if provider_preference() != "gpu":
                    return
                _get_session()
                if _session_provider != "gpu":
                    _release_session_resources()
            except Exception as exc:
                _session_fallback_reason = f"GPU 预热失败: {type(exc).__name__}"
                _release_session_resources()

        _warmup_thread = threading.Thread(
            target=worker,
            name="modal-3d-rembg-gpu-warmup",
            daemon=True,
        )
        _warmup_thread.start()
        return True
