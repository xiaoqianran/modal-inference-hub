from __future__ import annotations

import ctypes
import os
import platform
import shutil
import subprocess
from ctypes import wintypes

from agent.storage import data_dir


def _memory_mib() -> int | None:
    if os.name == "nt":
        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", wintypes.DWORD),
                ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.ullTotalPhys // (1024 * 1024))
        return None
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        pages = os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, OSError, ValueError):
        return None
    return int(page_size * pages // (1024 * 1024))


def detect_hardware() -> dict:
    disk = shutil.disk_usage(data_dir())
    result = {
        "platform": platform.system(),
        "machine": platform.machine(),
        "memory_mib": _memory_mib(),
        "disk_free_mib": int(disk.free // (1024 * 1024)),
        "gpus": [],
    }
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return result

    for line in proc.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) == 3:
            name, memory_mib, driver = parts
            try:
                memory = int(memory_mib)
            except ValueError:
                continue
            result["gpus"].append({"name": name, "memory_mib": memory, "driver": driver})
    return result
