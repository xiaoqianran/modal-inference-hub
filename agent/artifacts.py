from __future__ import annotations

import hashlib
import io
from collections.abc import Iterable
from pathlib import PurePosixPath

import modal

from agent.modal_client import client

VOLUME_NAME = "modal-3d-artifacts"


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
