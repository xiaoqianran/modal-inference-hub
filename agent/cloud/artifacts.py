from __future__ import annotations

import hashlib
import io
import tempfile
import uuid
from pathlib import Path

import modal

from agent.cloud.registry import ARTIFACTS_VOLUME
from agent.modal_client import require_client

MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024


def _volume():
    return modal.Volume.from_name(ARTIFACTS_VOLUME, client=require_client())


def _validate_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    if not normalized or normalized.startswith("/") or ".." in normalized.split("/"):
        raise ValueError("artifact path must be relative")
    return normalized


def put(data: bytes, suffix: str = ".png") -> dict:
    if not data:
        raise ValueError("artifact data is empty")
    allowed = ".abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
    if not suffix.startswith(".") or len(suffix) > 16 or any(c not in allowed for c in suffix):
        raise ValueError("invalid artifact suffix")
    digest = hashlib.sha256(data).hexdigest()
    path = f"client-inputs/{digest[:2]}/{digest}-{uuid.uuid4().hex[:8]}{suffix.lower()}"
    stream = io.BytesIO(data)
    with _volume().batch_upload() as batch:
        batch.put_file(stream, path)
    return {"path": path, "bytes": len(data), "sha256": digest}


def download_to_temp(path: str) -> Path:
    path = _validate_path(path)
    suffix = Path(path).suffix
    temp = tempfile.NamedTemporaryFile(prefix="modal-3d-artifact-", suffix=suffix, delete=False)
    temp_path = Path(temp.name)
    try:
        with temp:
            _volume().read_file_into_fileobj(path, temp)
        if temp_path.stat().st_size > MAX_DOWNLOAD_BYTES:
            raise ValueError("artifact download exceeds 512 MiB")
        return temp_path
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
