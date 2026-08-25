"""BiRefNet model storage, verification and resumable download."""
from __future__ import annotations

import hashlib
import threading
import urllib.request
from pathlib import Path
from agent.storage import data_dir

ENGINE = "birefnet-general-lite"
MODEL_URL = "https://github.com/danielgatis/rembg/releases/download/v0.0.0/BiRefNet-general-bb_swin_v1_tiny-epoch_232.onnx"
MODEL_MD5 = "4fab47adc4ff364be1713e97b7e66334"
MODEL_BYTES = 224_005_088
_download_lock = threading.Lock()
_download_state_lock = threading.RLock()
_prepare_thread_lock = threading.RLock()
_prepare_thread: threading.Thread | None = None
_download_state = {"status": "idle", "downloaded_bytes": 0, "total_bytes": MODEL_BYTES, "error": None, "integrity": "unverified"}
_verified_model_signature: tuple[int, int] | None = None

def rembg_home() -> Path:
    path = data_dir() / "rembg"
    path.mkdir(parents=True, exist_ok=True)
    return path


def model_path() -> Path:
    return rembg_home() / "models" / ENGINE / f"{ENGINE}.onnx"


def partial_model_path() -> Path:
    return model_path().with_suffix(".onnx.partial")


def _set_download_state(**changes) -> None:
    with _download_state_lock:
        _download_state.update(changes)


def download_status() -> dict:
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
        if model_path().is_file() and download_status()["integrity"] == "verified":
            return download_status()
        if _prepare_thread is not None and _prepare_thread.is_alive():
            return download_status()

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
        return download_status()
