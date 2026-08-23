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
            return {"sam_mode": "auto"}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"sam_mode": "auto"}
        mode = value.get("sam_mode", "auto")
        return {"sam_mode": mode if mode in _SAM_MODES else "auto"}


def set_sam_mode(mode: str) -> dict:
    if mode not in _SAM_MODES:
        raise ValueError("SAM 模式必须是 auto、cloud 或 local")
    value = {"sam_mode": mode}
    with _lock:
        path = _path()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    return value
