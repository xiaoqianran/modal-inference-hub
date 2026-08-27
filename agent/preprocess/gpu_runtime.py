"""On-demand NVIDIA CUDA runtime pack for the Windows desktop build.

The desktop installer deliberately does not embed NVIDIA's multi-gigabyte wheel
payload.  CUDA remains the preferred ONNX Runtime backend: when a compatible
system/Python CUDA runtime is unavailable, the required signed PyPI wheels are
downloaded once into LOCALAPPDATA, SHA-256 verified, and only their DLLs are
extracted.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import urllib.request
import zipfile
from pathlib import Path

from agent.storage import data_dir

_RUNTIME_VERSION = 1
_PYPI = "https://pypi.org/pypi/{name}/{version}/json"
_USER_AGENT = "modal-3D-client/0.4 cuda-runtime-bootstrap"

# Versions match the CUDA 12 / cuDNN 9 stack used by onnxruntime-gpu 1.24.x.
# Hashes are the official win_amd64 wheels locked by this project.
_PACKAGES = (
    # These are the four runtime families the previous bundled CUDA build used
    # successfully. Keep the pack minimal: no compiler/NVRTC payload is needed
    # for ONNX Runtime inference.
    ("nvidia-cublas-cu12", "12.9.2.10", "623f43027d40d44ceadf0043f002bd25cf353e8f13ce90b9a87057019f560661"),
    ("nvidia-cuda-runtime-cu12", "12.9.79", "8e018af8fa02363876860388bd10ccb89eb9ab8fb0aa749aaf58430a9f7c4891"),
    ("nvidia-cudnn-cu12", "9.24.0.43", "cbd41a0ab084422c936dc9fb2fc89be5ea9a85bc421c6f23d0243bdfc945fbef"),
    ("nvidia-cufft-cu12", "11.4.1.4", "8e5bfaac795e93f80611f807d42844e8e27e340e0cde270dcb6c65386d795b80"),
)

_lock = threading.Lock()
_state_lock = threading.Lock()
_state: dict = {"status": "not-installed", "package": None, "error": None}


def root() -> Path:
    return data_dir() / "gpu-runtime" / "cuda12-cudnn9-v1"


def bin_dir() -> Path:
    return root() / "bin"


def _marker() -> Path:
    return root() / "complete.json"


def ready() -> bool:
    marker = _marker()
    if not marker.is_file() or not bin_dir().is_dir():
        return False
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    expected_packages = [[name, version] for name, version, _sha256 in _PACKAGES]
    return payload.get("version") == _RUNTIME_VERSION and payload.get("packages") == expected_packages


def status() -> dict:
    with _state_lock:
        result = dict(_state)
    result["ready"] = ready()
    result["path"] = str(bin_dir()) if result["ready"] else None
    if result["ready"] and result["status"] == "not-installed":
        result["status"] = "ready"
    return result


def _set_state(**values) -> None:
    with _state_lock:
        _state.update(values)


def _wheel_metadata(name: str, version: str, expected_sha: str) -> tuple[str, str]:
    request = urllib.request.Request(_PYPI.format(name=name, version=version), headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        document = json.load(response)
    candidates = [
        item for item in document.get("urls", [])
        if item.get("filename", "").endswith("-win_amd64.whl")
    ]
    if len(candidates) != 1:
        raise RuntimeError(f"{name} {version} 没有唯一的 Windows x64 wheel")
    item = candidates[0]
    actual = str(item.get("digests", {}).get("sha256", "")).lower()
    if actual != expected_sha:
        raise RuntimeError(f"{name} {version} 的 PyPI SHA-256 与内置清单不一致")
    return str(item["url"]), str(item["filename"])


def _download(url: str, destination: Path, expected_sha: str) -> None:
    temporary = destination.with_suffix(destination.suffix + ".part")
    digest = hashlib.sha256()
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
            digest.update(chunk)
    if digest.hexdigest().lower() != expected_sha:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"{destination.name} 下载后 SHA-256 校验失败")
    temporary.replace(destination)


def _extract_dlls(wheel: Path, destination: Path) -> int:
    count = 0
    with zipfile.ZipFile(wheel) as archive:
        for member in archive.infolist():
            if member.is_dir() or not member.filename.lower().endswith(".dll"):
                continue
            name = Path(member.filename).name
            target = destination / name
            with archive.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            count += 1
    return count


def ensure_runtime() -> Path:
    """Return a DLL directory, installing the CUDA runtime pack once if needed."""
    if os.name != "nt":
        raise RuntimeError("CUDA runtime pack 仅用于 Windows")
    if ready():
        return bin_dir()

    with _lock:
        if ready():
            return bin_dir()
        target_root = root()
        staging = target_root.with_name(target_root.name + ".installing")
        shutil.rmtree(staging, ignore_errors=True)
        (staging / "bin").mkdir(parents=True, exist_ok=True)
        _set_state(status="installing", package=None, error=None)
        try:
            dll_count = 0
            for name, version, sha256 in _PACKAGES:
                _set_state(package=f"{name}=={version}")
                url, filename = _wheel_metadata(name, version, sha256)
                wheel = staging / filename
                _download(url, wheel, sha256)
                dll_count += _extract_dlls(wheel, staging / "bin")
                wheel.unlink(missing_ok=True)
            if dll_count == 0:
                raise RuntimeError("CUDA runtime pack 未提取到 DLL")
            marker = {
                "version": _RUNTIME_VERSION,
                "packages": [p[0:2] for p in _PACKAGES],
                "dll_count": dll_count,
            }
            (staging / "complete.json").write_text(json.dumps(marker, indent=2), encoding="utf-8")
            shutil.rmtree(target_root, ignore_errors=True)
            staging.replace(target_root)
            _set_state(status="ready", package=None, error=None)
            return bin_dir()
        except Exception as exc:
            shutil.rmtree(staging, ignore_errors=True)
            _set_state(status="failed", package=None, error=f"{type(exc).__name__}: {exc}")
            raise
