"""产物（artifact）的上传、下载、校验、缓存与租约管理。

设计核心是「不信任远端」：任何从云端 Volume 拿回的字节，落盘前都必须
通过完整性校验，落盘后随时可重算哈希复核。具体手段：

1. 内容寻址：本地缓存文件名 = sha256(内容)，天然去重，且文件名即完整性承诺；
2. 原子提交：先写 .part 临时文件，校验通过后 os.replace 原子改名，失败不落半成品；
3. 租约（lease）：读取中的文件加引用计数，缓存清理（LRU）跳过被租约保护的文件；
4. 幂等恢复：本地缓存被清理后，可凭 descriptor 从远端重新拉取（见 jobs.artifact）。

canonical（PNG）与 GLB 产物共用这套机制；唯一区别是校验头不同（PNG magic vs glTF v2）。
"""

from __future__ import annotations

import hashlib
import io
import os
import struct
import threading
import time
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import modal

from agent.constants import ARTIFACTS_VOLUME
from agent.modal_client import client
from agent.storage import data_dir

VOLUME_NAME = ARTIFACTS_VOLUME
DEFAULT_CACHE_BUDGET_BYTES = 2 * 1024 * 1024 * 1024
_CHUNK_SIZE = 1024 * 1024
_lease_lock = threading.RLock()
_leases: dict[Path, int] = {}


class ArtifactValidationError(ValueError):
    pass


def normalize_path(path: str) -> str:
    value = PurePosixPath(path)
    if not path or value.is_absolute() or ".." in value.parts:
        raise ValueError("artifact path 必须是安全的相对路径")
    return value.as_posix()


def _volume() -> modal.Volume:
    return modal.Volume.from_name(VOLUME_NAME, client=client())


def put(data: bytes, suffix: str = ".bin") -> dict:
    if not data:
        raise ValueError("artifact 不能为空")
    digest = hashlib.sha256(data).hexdigest()
    suffix = suffix.lower() if suffix.startswith(".") else f".{suffix.lower()}"
    path = f"client-inputs/{digest}{suffix}"
    with _volume().batch_upload(force=True) as batch:
        batch.put_file(io.BytesIO(data), path)
    return {"path": path, "bytes": len(data), "sha256": digest}


def read(path: str) -> Iterable[bytes]:
    return _volume().read_file(normalize_path(path))


def content_id(prefix: str, kind: str, sha256: str) -> str:
    digest = _digest(sha256)
    identity = uuid.uuid5(uuid.NAMESPACE_URL, f"modal-3d:{kind}:{digest}").hex
    return f"{prefix}_{identity}"


def describe_remote_png(path: str, expected_bytes: int | None = None) -> dict:
    remote_path = normalize_path(path)
    digest = hashlib.sha256()
    total = 0
    signature = bytearray()
    for chunk in read(remote_path):
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise ArtifactValidationError("artifact stream 返回了非 bytes 数据")
        if len(signature) < 8:
            signature.extend(chunk[: 8 - len(signature)])
        digest.update(chunk)
        total += len(chunk)
    if expected_bytes is not None and total != expected_bytes:
        raise ArtifactValidationError(
            f"canonical bytes 不一致：expected={expected_bytes}, actual={total}"
        )
    if bytes(signature) != b"\x89PNG\r\n\x1a\n":
        raise ArtifactValidationError("canonical 不是有效 PNG")
    sha256 = digest.hexdigest()
    return {
        "id": content_id("can", "canonical", sha256),
        "role": "canonical-rgba",
        "mime": "image/png",
        "bytes": total,
        "sha256": sha256,
        "path": remote_path,
    }


def _cache_root() -> Path:
    root = data_dir() / "cache" / "sha256"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _digest(value: str) -> str:
    digest = value.lower()
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise ArtifactValidationError("artifact sha256 无效")
    return digest


def cache_path(sha256: str) -> Path:
    digest = _digest(sha256)
    return _cache_root() / digest[:2] / digest


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_glb(path: Path, expected_bytes: int | None = None) -> int:
    actual = path.stat().st_size
    if expected_bytes is not None and actual != expected_bytes:
        raise ArtifactValidationError(
            f"artifact bytes 不一致：expected={expected_bytes}, actual={actual}"
        )
    with path.open("rb") as handle:
        header = handle.read(12)
    if len(header) != 12:
        raise ArtifactValidationError("artifact GLB 已截断")
    magic, version, declared = struct.unpack("<4sII", header)
    if magic != b"glTF" or version != 2 or declared != actual:
        raise ArtifactValidationError("artifact 不是有效的 glTF Binary v2")
    return actual


def _artifact_id(model: str, sha256: str) -> str:
    identity = uuid.uuid5(uuid.NAMESPACE_URL, f"modal-3d:artifact:{model}:{sha256}").hex
    return f"art_{identity}"


def cache_remote(remote_path: str, descriptor: dict, model: str) -> tuple[dict, Path]:
    """从云端 Volume 下载 GLB，校验后写入内容寻址缓存。

    流程：流式下载到 .part → 校验 glTF v2 头与字节数 → 校验 SHA-256 →
    原子改名到 cache/sha256/{前2位}/{完整哈希}。全程任一校验失败即抛
    ArtifactValidationError 并清理临时文件，绝不留下半成品。
    返回（脱敏后的公开 descriptor，本地缓存路径）。
    """
    remote_path = normalize_path(remote_path)
    expected_bytes = descriptor.get("bytes")
    if expected_bytes is not None and (
        not isinstance(expected_bytes, int) or isinstance(expected_bytes, bool) or expected_bytes <= 0
    ):
        raise ArtifactValidationError("artifact bytes 无效")
    expected_sha = descriptor.get("sha256")
    if expected_sha is not None:
        if not isinstance(expected_sha, str):
            raise ArtifactValidationError("artifact sha256 无效")
        expected_sha = _digest(expected_sha)

    temporary_root = _cache_root() / ".parts"
    temporary_root.mkdir(parents=True, exist_ok=True)
    partial = temporary_root / f"{uuid.uuid4().hex}.part"
    digest = hashlib.sha256()
    total = 0
    try:
        with partial.open("wb") as output:
            for chunk in read(remote_path):
                if not isinstance(chunk, (bytes, bytearray, memoryview)):
                    raise ArtifactValidationError("artifact stream 返回了非 bytes 数据")
                output.write(chunk)
                digest.update(chunk)
                total += len(chunk)
            output.flush()
            os.fsync(output.fileno())
        _validate_glb(partial, expected_bytes)
        actual_sha = digest.hexdigest()
        if expected_sha is not None and actual_sha != expected_sha:
            raise ArtifactValidationError("artifact SHA-256 校验失败")

        final = cache_path(actual_sha)
        final.parent.mkdir(parents=True, exist_ok=True)
        if final.is_file():
            _validate_glb(final, total)
            if _sha256_file(final) != actual_sha:
                raise ArtifactValidationError("本地 cache SHA-256 校验失败")
            partial.unlink(missing_ok=True)
        else:
            os.replace(partial, final)
        final.touch()
    except Exception:
        partial.unlink(missing_ok=True)
        raise

    artifact_id = descriptor.get("id")
    if artifact_id is not None and (not isinstance(artifact_id, str) or not artifact_id):
        raise ArtifactValidationError("artifact id 无效")
    public = {
        key: value
        for key, value in descriptor.items()
        if key not in {"path", "remote_path", "internal_path"}
    }
    public.update(
        {
            "id": artifact_id or _artifact_id(model, actual_sha),
            "role": public.get("role") or "primary-glb",
            "mime": "model/gltf-binary",
            "bytes": total,
            "sha256": actual_sha,
        }
    )
    public.setdefault(
        "producer",
        {"model": model, "worker_app": None, "revision": None},
    )
    public.setdefault("created_at", datetime.now(UTC).isoformat())
    public.setdefault("expires_at", None)
    cleanup_cache(protected={final})
    return public, final


def verified_path(descriptor: dict) -> Path:
    sha256 = descriptor.get("sha256")
    size = descriptor.get("bytes")
    if not isinstance(sha256, str) or not isinstance(size, int):
        raise ArtifactValidationError("artifact descriptor 缺少 sha256/bytes")
    path = cache_path(sha256)
    if not path.is_file():
        raise FileNotFoundError(path)
    _validate_glb(path, size)
    if _sha256_file(path) != _digest(sha256):
        raise ArtifactValidationError("本地 cache SHA-256 校验失败")
    path.touch()
    return path


def lease(path: Path) -> Path:
    """对缓存文件加租约（引用计数 +1），返回原路径。

    租约保护期间，cleanup_cache 不会删除该文件；用完必须调用 release 归还。
    """
    with _lease_lock:
        _leases[path] = _leases.get(path, 0) + 1
    return path


def acquire(descriptor: dict) -> Path:
    """校验 descriptor 指向的缓存文件后，再加租约返回路径。

    等价于 verified_path + lease 的组合，用于「读文件」场景的一步到位。
    """
    return lease(verified_path(descriptor))


def release(path: Path) -> None:
    """归还租约（引用计数 -1），计数归零后允许缓存清理回收。"""
    with _lease_lock:
        count = _leases.get(path, 0)
        if count <= 1:
            _leases.pop(path, None)
        else:
            _leases[path] = count - 1


def _budget_bytes() -> int:
    value = os.environ.get("MODAL_3D_CACHE_BUDGET_BYTES")
    if value:
        try:
            budget = int(value)
            if budget > 0:
                return budget
        except ValueError:
            pass
    return DEFAULT_CACHE_BUDGET_BYTES


def cleanup_cache(*, protected: set[Path] | None = None) -> dict:
    root = _cache_root()
    protected = {path.resolve() for path in (protected or set())}
    cutoff = time.time() - 24 * 60 * 60
    parts = root / ".parts"
    if parts.is_dir():
        for partial in parts.glob("*.part"):
            try:
                if partial.stat().st_mtime < cutoff:
                    partial.unlink()
            except FileNotFoundError:
                pass

    files = [
        path
        for prefix in root.iterdir()
        if prefix.is_dir() and prefix.name != ".parts"
        for path in prefix.iterdir()
        if path.is_file()
    ]
    total = sum(path.stat().st_size for path in files)
    budget = _budget_bytes()
    deleted = 0
    with _lease_lock:
        leased = {path.resolve() for path in _leases}
    for path in sorted(files, key=lambda item: item.stat().st_mtime):
        resolved = path.resolve()
        if total <= budget:
            break
        if resolved in protected or resolved in leased:
            continue
        try:
            size = path.stat().st_size
            path.unlink()
        except FileNotFoundError:
            continue
        total -= size
        deleted += 1
    return {"bytes": total, "budget_bytes": budget, "deleted": deleted}
