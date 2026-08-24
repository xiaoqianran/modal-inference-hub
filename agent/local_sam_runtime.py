from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import modal

from agent.modal_client import client
from agent.storage import data_dir

BOOTSTRAP_VERSION = "1"
BOOTSTRAP_RELEASE_TAG = "local-sam-runtime-v1"
BOOTSTRAP_ASSET = "modal-3D-local-sam-bootstrap-windows-x86_64-v1.zip"
BOOTSTRAP_SHA256 = "1392402accd8985cbecabe62f766a847aee613c868e3dfb5191253bb4db0d73d"
BOOTSTRAP_RELEASE_BASE = (
    f"https://github.com/xiaoqianran/modal-3D-client/releases/download/{BOOTSTRAP_RELEASE_TAG}"
)
WEIGHTS_VOLUME = "modal-3d-sam31-weights"
WEIGHTS_PATH = "sam31/sam3.1_multiplex.pt"
CHECKPOINT_BYTES = 3_502_755_717
CHECKPOINT_SHA256 = "0567debeec80ba4ac6369540c6c248025283cb3ff2b92827509e57e2b3541cb6"

_lock = threading.RLock()
_process: subprocess.Popen[str] | None = None
_port: int | None = None
_token: str | None = None
_last_health: dict | None = None
_install_thread: threading.Thread | None = None


def root() -> Path:
    value = data_dir() / "local-sam"
    value.mkdir(parents=True, exist_ok=True)
    return value


def runtime_dir() -> Path:
    return root() / "runtime"


def checkpoint_path() -> Path:
    return root() / "sam3.1_multiplex.pt"


def _status_path() -> Path:
    return root() / "status.json"


def _write_status(**values) -> None:
    payload = {"updated_at": time.time(), **values}
    path = _status_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def _read_status() -> dict:
    path = _status_path()
    if not path.is_file():
        return {"state": "not_installed"}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"state": "unknown"}


def _download(url: str, target: Path, *, expected_sha256: str | None = None) -> dict:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    digest = hashlib.sha256()
    total = 0
    request = urllib.request.Request(url, headers={"User-Agent": "modal-3D-client"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as output:
            while chunk := response.read(8 * 1024 * 1024):
                output.write(chunk)
                digest.update(chunk)
                total += len(chunk)
            output.flush()
            os.fsync(output.fileno())
        actual = digest.hexdigest()
        if expected_sha256 and actual.lower() != expected_sha256.lower():
            raise ValueError(f"SHA256 不匹配：预期 {expected_sha256}，实际 {actual}")
        partial.replace(target)
        return {"bytes": total, "sha256": actual}
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def _safe_extract(zip_path: Path, destination: Path) -> None:
    staging = destination.with_name(destination.name + ".new")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            path = (staging / member.filename).resolve()
            try:
                path.relative_to(staging.resolve())
            except ValueError as exc:
                raise ValueError("bootstrap archive 包含越界路径") from exc
        archive.extractall(staging)
    backup = destination.with_name(destination.name + ".old")
    shutil.rmtree(backup, ignore_errors=True)
    if destination.exists():
        destination.replace(backup)
    staging.replace(destination)
    shutil.rmtree(backup, ignore_errors=True)


def _recover_runtime_backup() -> None:
    target = runtime_dir()
    backup = target.with_name(target.name + ".old")
    if target.exists() or not backup.exists():
        return
    backup.replace(target)


def _activate_runtime(staging: Path) -> None:
    target = runtime_dir()
    backup = target.with_name(target.name + ".old")
    _recover_runtime_backup()
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)
    moved_old = False
    try:
        if target.exists():
            target.replace(backup)
            moved_old = True
        staging.replace(target)
    except Exception:
        if moved_old and backup.exists() and not target.exists():
            backup.replace(target)
        raise
    shutil.rmtree(backup, ignore_errors=True)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(16 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _checkpoint_marker() -> Path:
    return checkpoint_path().with_suffix(".pt.sha256")


def _checkpoint_valid() -> bool:
    path = checkpoint_path()
    marker = _checkpoint_marker()
    if not path.is_file() or path.stat().st_size != CHECKPOINT_BYTES or not marker.is_file():
        return False
    try:
        return marker.read_text(encoding="ascii").strip().lower() == CHECKPOINT_SHA256
    except OSError:
        return False


def _runtime_version(directory: Path | None = None) -> str | None:
    if directory is None:
        _recover_runtime_backup()
        directory = runtime_dir()
    manifest = directory / "manifest.json"
    installed = directory / "installed.json"
    python = directory / "python" / "python.exe"
    install = directory / "install.ps1"
    if not all(path.is_file() for path in (manifest, installed, python, install)):
        return None
    try:
        manifest_value = json.loads(manifest.read_text(encoding="utf-8-sig"))
        installed_value = json.loads(installed.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    manifest_version = str(manifest_value.get("version", ""))
    installed_version = str(installed_value.get("version", ""))
    return manifest_version if manifest_version and manifest_version == installed_version else None


def _runtime_files_valid(directory: Path | None = None) -> bool:
    return _runtime_version(directory) == BOOTSTRAP_VERSION


def sync_checkpoint() -> dict:
    final = checkpoint_path()
    marker = _checkpoint_marker()
    if _checkpoint_valid():
        return {"bytes": final.stat().st_size, "sha256": CHECKPOINT_SHA256, "cached": True}

    if final.is_file() and final.stat().st_size == CHECKPOINT_BYTES:
        actual = _sha256_file(final)
        if actual == CHECKPOINT_SHA256:
            marker.write_text(CHECKPOINT_SHA256, encoding="ascii")
            return {"bytes": CHECKPOINT_BYTES, "sha256": actual, "cached": True}
        final.unlink()
        marker.unlink(missing_ok=True)

    partial = final.with_suffix(".pt.part")
    partial.unlink(missing_ok=True)
    volume = modal.Volume.from_name(WEIGHTS_VOLUME, client=client())
    digest = hashlib.sha256()
    total = 0
    try:
        next_report = 64 * 1024 * 1024
        with partial.open("wb") as output:
            for chunk in volume.read_file(WEIGHTS_PATH):
                output.write(chunk)
                digest.update(chunk)
                total += len(chunk)
                if total >= next_report:
                    _write_status(
                        state="installing",
                        step="checkpoint",
                        downloaded_bytes=total,
                        checkpoint_bytes=CHECKPOINT_BYTES,
                    )
                    next_report += 64 * 1024 * 1024
            output.flush()
            os.fsync(output.fileno())
        if total != CHECKPOINT_BYTES:
            raise ValueError(f"SAM 3.1 checkpoint 大小异常：预期 {CHECKPOINT_BYTES}，实际 {total}")
        actual = digest.hexdigest()
        if actual != CHECKPOINT_SHA256:
            raise ValueError(f"SAM 3.1 checkpoint SHA256 不匹配：实际 {actual}")
        partial.replace(final)
        marker.write_text(CHECKPOINT_SHA256, encoding="ascii")
        return {"bytes": total, "sha256": actual, "cached": False}
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def install(
    *,
    bootstrap_url: str | None = None,
    bootstrap_sha256: str | None = None,
) -> dict:
    if os.name != "nt":
        raise RuntimeError("Local SAM runtime 目前只支持 Windows x86_64")
    stop()
    _recover_runtime_backup()
    _write_status(state="installing", step="bootstrap")
    cache = root() / "downloads"
    cache.mkdir(exist_ok=True)
    archive = cache / BOOTSTRAP_ASSET
    if bootstrap_url is None:
        bootstrap_url = f"{BOOTSTRAP_RELEASE_BASE}/{BOOTSTRAP_ASSET}"
    if bootstrap_sha256 is None:
        bootstrap_sha256 = BOOTSTRAP_SHA256
    _download(bootstrap_url, archive, expected_sha256=bootstrap_sha256)
    staging = runtime_dir().with_name(runtime_dir().name + ".installing")
    shutil.rmtree(staging, ignore_errors=True)
    _safe_extract(archive, staging)

    try:
        _write_status(state="installing", step="dependencies")
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if not powershell:
            raise RuntimeError("找不到 PowerShell，无法安装 Local SAM runtime")
        completed = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(staging / "install.ps1"),
                "-RuntimeDir",
                str(staging),
            ],
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout)[-4000:]
            _write_status(state="error", step="dependencies", error=detail)
            raise RuntimeError("Local SAM runtime 依赖安装失败")
        if not _runtime_files_valid(staging):
            raise RuntimeError("Local SAM runtime 版本验证失败")

        _write_status(state="installing", step="checkpoint")
        sync_checkpoint()
        if not _checkpoint_valid():
            raise RuntimeError("Local SAM checkpoint 验证失败")
        _activate_runtime(staging)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    _write_status(state="installed", step="ready")
    return status()


def _install_worker() -> None:
    try:
        install()
        _write_status(state="starting", step="health")
        health = start()
        _write_status(state="ready", step="ready", health=health)
    except Exception as exc:  # noqa: BLE001 - background installer must persist terminal error state.
        _write_status(state="error", step="failed", error=str(exc) or type(exc).__name__)


def begin_install() -> dict:
    global _install_thread
    with _lock:
        if _install_thread is not None and _install_thread.is_alive():
            return status()
        _install_thread = threading.Thread(
            target=_install_worker,
            name="local-sam-installer",
            daemon=True,
        )
        _install_thread.start()
    return status()




def _path_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except FileNotFoundError:
            pass
    return total


def uninstall() -> dict:
    with _lock:
        if _install_thread is not None and _install_thread.is_alive():
            raise RuntimeError("Local SAM 正在安装，请等待安装结束后再卸载")
    stop()

    targets = [
        runtime_dir(),
        runtime_dir().with_name(runtime_dir().name + ".installing"),
        runtime_dir().with_name(runtime_dir().name + ".old"),
        checkpoint_path(),
        _checkpoint_marker(),
        root() / "downloads",
        root() / "runtime.log",
        root() / "runtime.port",
    ]
    released = sum(_path_bytes(path) for path in targets)
    for path in targets:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    _write_status(state="not_installed", step="uninstalled")
    return {
        **status(),
        "released_bytes": released,
        "preserved_data": str(root() / "data"),
    }


def _request(path: str, payload: dict | None = None, *, timeout: float = 30) -> dict:
    with _lock:
        port, token = _port, _token
    if port is None or token is None:
        raise RuntimeError("Local SAM runtime 尚未运行")
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=body,
        method="POST" if payload is not None else "GET",
        headers={
            "X-Modal-3D-Local-SAM": token,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read()).get("detail")
        except (AttributeError, json.JSONDecodeError):
            detail = None
        raise RuntimeError(detail or f"Local SAM HTTP {exc.code}") from exc


def _runtime_log_tail(lines: int = 20) -> str:
    path = root() / "runtime.log"
    if not path.is_file():
        return ""
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(content[-lines:])[-4000:]


def start(*, health_timeout: float = 180) -> dict:
    global _process, _port, _token, _last_health
    if os.name != "nt":
        raise RuntimeError("Local SAM runtime 目前只支持 Windows x86_64")
    if not _runtime_files_valid() or not _checkpoint_valid():
        raise RuntimeError("Local SAM runtime 尚未完整安装")
    with _lock:
        if _process is not None and _process.poll() is None:
            try:
                return _request("/health", timeout=5)
            except RuntimeError:
                pass
        stop()
        handshake = root() / "runtime.port"
        handshake.unlink(missing_ok=True)
        token = secrets.token_hex(32)
        env = os.environ.copy()
        env.update(
            {
                "MODAL_3D_LOCAL_SAM_TOKEN": token,
                "MODAL_3D_LOCAL_SAM_HANDSHAKE": str(handshake),
                "MODAL_3D_LOCAL_SAM_DATA_DIR": str(root() / "data"),
                "MODAL_3D_LOCAL_SAM_CHECKPOINT": str(checkpoint_path()),
                "MODAL_3D_LOCAL_SAM_PROJECTS_DIR": str(data_dir() / "projects"),
                "PYTHONUTF8": "1",
                "MODAL_3D_LOCAL_SAM_PARENT_PID": str(os.getpid()),
            }
        )
        python = runtime_dir() / "python" / "python.exe"
        log_path = root() / "runtime.log"
        log = log_path.open("w", encoding="utf-8")
        try:
            _process = subprocess.Popen(
                [str(python), "-m", "local_sam_runtime.server"],
                cwd=runtime_dir(),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        finally:
            log.close()
        _token = token

    deadline = time.time() + health_timeout
    while time.time() < deadline:
        with _lock:
            process = _process
        if process is None or process.poll() is not None:
            detail = _runtime_log_tail()
            stop()
            raise RuntimeError(
                "Local SAM runtime 启动失败" + (f"：\n{detail}" if detail else "")
            )
        if handshake.is_file():
            try:
                port = int(handshake.read_text().strip())
                if 1 <= port <= 65535:
                    with _lock:
                        _port = port
                    health = _request("/health", timeout=10)
                    if health.get("ready"):
                        with _lock:
                            _last_health = health
                        _write_status(state="running", step="ready", health=health)
                        return health
            except (OSError, ValueError, RuntimeError):
                pass
        time.sleep(0.25)
    detail = _runtime_log_tail()
    stop()
    raise RuntimeError(
        "Local SAM runtime 健康检查超时" + (f"：\n{detail}" if detail else "")
    )


def stop() -> None:
    global _process, _port, _token, _last_health
    with _lock:
        process = _process
        _process = None
        _port = None
        _token = None
        _last_health = None
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    (root() / "runtime.port").unlink(missing_ok=True)


def installation_state() -> dict:
    installed_version = _runtime_version()
    runtime_installed = installed_version == BOOTSTRAP_VERSION
    checkpoint_installed = _checkpoint_valid()
    return {
        "runtime_installed": runtime_installed,
        "checkpoint_installed": checkpoint_installed,
        "installed": runtime_installed and checkpoint_installed,
        "installed_version": installed_version,
        "expected_version": BOOTSTRAP_VERSION,
        "update_available": bool(installed_version and installed_version != BOOTSTRAP_VERSION),
    }


def status() -> dict:
    with _lock:
        process = _process
        running = process is not None and process.poll() is None and _port is not None
        health = _last_health
        installing = _install_thread is not None and _install_thread.is_alive()
    return {
        **_read_status(),
        **installation_state(),
        "checkpoint_bytes": CHECKPOINT_BYTES,
        "installing": installing,
        "running": running,
        "ready": bool(running and health and health.get("ready")),
        "health": health,
    }


def request_segment(image_path: Path, concept: str, max_candidates: int) -> dict:
    start()
    return _request(
        "/segment",
        {"image_path": str(image_path), "concept": concept, "max_candidates": max_candidates},
        timeout=180,
    )


def request_refine(scene_id: str, concept: str, boxes: list[dict], max_candidates: int) -> dict:
    start()
    return _request(
        "/refine",
        {"scene_id": scene_id, "concept": concept, "boxes": boxes, "max_candidates": max_candidates},
        timeout=120,
    )


def request_materialize(
    scene_id: str,
    selection_id: str,
    candidate_id: str,
    output_size: int,
) -> dict:
    start()
    return _request(
        "/materialize",
        {
            "scene_id": scene_id,
            "selection_id": selection_id,
            "candidate_id": candidate_id,
            "output_size": output_size,
        },
        timeout=60,
    )
