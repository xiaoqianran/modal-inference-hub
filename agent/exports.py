from __future__ import annotations

import hashlib
import os
import time
import uuid
from pathlib import Path

from agent import artifacts
from agent.storage import data_dir

_MAX_EXPORT_AGE_SECONDS = 24 * 60 * 60


def _root() -> Path:
    root = data_dir() / "exports"
    root.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - _MAX_EXPORT_AGE_SECONDS
    for path in root.iterdir():
        if path.suffix not in {".glb", ".part"}:
            continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except FileNotFoundError:
            pass
    return root


def prepare(artifact_path: str) -> dict:
    export_id = uuid.uuid4().hex
    final = _root() / f"{export_id}.glb"
    partial = final.with_suffix(".part")
    total = 0
    digest = hashlib.sha256()
    head = bytearray()
    try:
        with partial.open("wb") as output:
            for chunk in artifacts.read(artifact_path):
                if len(head) < 8:
                    head.extend(chunk[: 8 - len(head)])
                output.write(chunk)
                digest.update(chunk)
                total += len(chunk)
            output.flush()
            os.fsync(output.fileno())
        if bytes(head[:4]) != b"glTF" or int.from_bytes(head[4:8], "little") != 2:
            raise ValueError("远程 artifact 不是有效的 glTF Binary v2")
        partial.replace(final)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    return {
        "id": export_id,
        "bytes": total,
        "sha256": digest.hexdigest(),
    }
