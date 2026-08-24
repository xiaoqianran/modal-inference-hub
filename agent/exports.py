"""GLB 导出准备：把已验证的缓存产物暴露给 Tauri 原生保存。

零拷贝设计：优先用硬链接（os.link）把缓存文件直接「指」到 exports 目录，
避免复制几十 MB 的 GLB 字节；硬链接失败（如跨盘）才回退到流式复制。
导出文件按 24 小时超时清理。
"""

from __future__ import annotations

import os
import shutil
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


def prepare(source: Path, descriptor: dict) -> dict:
    verified = artifacts.verified_path(descriptor)
    if verified != source:
        raise artifacts.ArtifactValidationError("artifact cache path 与 descriptor 不一致")
    export_id = uuid.uuid4().hex
    final = _root() / f"{export_id}.glb"
    try:
        os.link(source, final)
    except OSError:
        partial = final.with_suffix(".part")
        try:
            with source.open("rb") as src, partial.open("wb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
                dst.flush()
                os.fsync(dst.fileno())
            os.replace(partial, final)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
    return {
        "id": export_id,
        "bytes": descriptor["bytes"],
        "sha256": descriptor["sha256"],
    }
