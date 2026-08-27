from __future__ import annotations

import os
import tempfile
from pathlib import Path

_APP_DIR = "modal-inference-hub"
_LEGACY_APP_DIR = "modal-3D-client"


def _platform_data_root() -> Path:
    if os.name == "nt":
        if root := os.environ.get("LOCALAPPDATA"):
            return Path(root)
    elif root := os.environ.get("XDG_DATA_HOME"):
        return Path(root)

    try:
        home = Path.home()
    except (OSError, RuntimeError):
        return Path(tempfile.gettempdir())
    return home / ("AppData/Local" if os.name == "nt" else ".local/share")


def data_dir() -> Path:
    override = os.environ.get("MODAL_INFERENCE_HUB_DATA_DIR") or os.environ.get(
        "MODAL_3D_AGENT_DATA_DIR"
    )
    if override:
        path = Path(override)
    else:
        root = _platform_data_root()
        current = root / _APP_DIR
        legacy = root / _LEGACY_APP_DIR
        path = legacy if legacy.exists() and not current.exists() else current
    path.mkdir(parents=True, exist_ok=True)
    return path
