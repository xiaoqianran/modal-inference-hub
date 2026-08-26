from __future__ import annotations

import ntpath
import os
import sys


def normalize_windows_environment() -> None:
    """补齐被精简宿主移除的标准 Windows 路径变量。"""
    if sys.platform != "win32":
        return

    if not os.environ.get("SystemDrive"):
        system_root = os.environ.get("SystemRoot") or os.environ.get("windir")
        drive, _ = ntpath.splitdrive(system_root or "")
        if drive:
            os.environ["SystemDrive"] = drive

    system_drive = os.environ.get("SystemDrive")
    if not system_drive:
        return
    os.environ.setdefault("ProgramData", ntpath.join(system_drive + "\\", "ProgramData"))
    os.environ.setdefault("ALLUSERSPROFILE", os.environ["ProgramData"])
