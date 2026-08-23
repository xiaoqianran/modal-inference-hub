from __future__ import annotations

from agent.hardware import detect_hardware
from agent.modal_client import connected
from agent.settings import get_settings
from agent.storage import data_dir

LOCAL_SAM_MIN_VRAM_MIB = 6144
LOCAL_SAM_CHECKPOINT_BYTES = 3_502_781_787


def local_sam_status(hardware: dict | None = None) -> dict:
    hardware = hardware or detect_hardware()
    eligible_gpu = next(
        (
            gpu
            for gpu in hardware.get("gpus", [])
            if int(gpu.get("memory_mib", 0)) >= LOCAL_SAM_MIN_VRAM_MIB
        ),
        None,
    )
    root = data_dir() / "local-sam"
    manifest = root / "runtime.json"
    checkpoint = root / "sam3.1_multiplex.pt"
    installed = manifest.is_file() and checkpoint.is_file()

    if eligible_gpu is None:
        reason = "未检测到至少 6 GiB VRAM 的 NVIDIA GPU"
    elif not installed:
        reason = "本地 SAM 3.1 runtime 尚未安装"
    else:
        reason = "本地 SAM runtime 包格式尚未启用"

    # The installer/runtime protocol is a separate milestone. Do not report local
    # availability until the executable contract is implemented and health-checked.
    return {
        "available": False,
        "installed": installed,
        "hardware_eligible": eligible_gpu is not None,
        "reason": reason,
        "min_vram_mib": LOCAL_SAM_MIN_VRAM_MIB,
        "checkpoint_bytes": LOCAL_SAM_CHECKPOINT_BYTES,
        "gpu": eligible_gpu,
    }


def capabilities() -> dict:
    hardware = detect_hardware()
    local = local_sam_status(hardware)
    cloud = {"available": connected()}
    mode = get_settings()["sam_mode"]
    if mode == "local":
        effective = "local" if local["available"] else None
    elif mode == "cloud":
        effective = "cloud" if cloud["available"] else None
    else:
        effective = "local" if local["available"] else "cloud" if cloud["available"] else None
    return {
        "hardware": hardware,
        "sam": {
            "mode": mode,
            "effective": effective,
            "local": local,
            "cloud": cloud,
        },
    }
