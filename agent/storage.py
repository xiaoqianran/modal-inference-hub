from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    if root := os.environ.get("MODAL_3D_AGENT_DATA_DIR"):
        path = Path(root)
    elif os.name == "nt" and (root := os.environ.get("LOCALAPPDATA")):
        path = Path(root) / "modal-3D-client"
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        path = root / "modal-3D-client"
    path.mkdir(parents=True, exist_ok=True)
    return path
