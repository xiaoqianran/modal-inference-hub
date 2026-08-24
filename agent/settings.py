from __future__ import annotations

import json
import threading
from pathlib import Path

from agent.storage import data_dir

_SAM_MODES = {"auto", "cloud", "local"}
_lock = threading.RLock()


def _path() -> Path:
    return data_dir() / "settings.json"


def get_settings() -> dict:
    with _lock:
        path = _path()
        if not path.is_file():
            return {"sam_mode": "auto", "local_sam_root": None}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"sam_mode": "auto", "local_sam_root": None}
        mode = value.get("sam_mode", "auto")
        root = value.get("local_sam_root")
        return {
            "sam_mode": mode if mode in _SAM_MODES else "auto",
            "local_sam_root": root if isinstance(root, str) and root.strip() else None,
        }


def _write(value: dict) -> dict:
    path = _path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def set_sam_mode(mode: str) -> dict:
    if mode not in _SAM_MODES:
        raise ValueError("SAM 模式必须是 auto、cloud 或 local")
    with _lock:
        value = get_settings()
        value["sam_mode"] = mode
        _write(value)
    return {"sam_mode": mode, "local_sam_root": value["local_sam_root"]}


def set_local_sam_root(path: str) -> dict:
    with _lock:
        value = get_settings()
        value["local_sam_root"] = path
        _write(value)
    return value
