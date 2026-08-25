from __future__ import annotations

from agent.hardware import detect_hardware
from agent.rembg_preprocess import status as rembg_status


def capabilities() -> dict:
    return {
        "hardware": detect_hardware(),
        "preprocessing": {
            "kind": "rembg",
            **rembg_status(),
        },
    }
