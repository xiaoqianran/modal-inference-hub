from __future__ import annotations

import platform
import subprocess


def detect_hardware() -> dict:
    result = {
        "platform": platform.system(),
        "machine": platform.machine(),
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
            result["gpus"].append(
                {
                    "name": name,
                    "memory_mib": memory,
                    "driver": driver,
                }
            )
    return result
