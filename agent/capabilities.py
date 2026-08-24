from __future__ import annotations

from agent import local_sam_runtime
from agent.hardware import detect_hardware
from agent.modal_client import connected
from agent.settings import get_settings

LOCAL_SAM_MIN_VRAM_MIB = 6144
LOCAL_SAM_MIN_DISK_MIB = 12 * 1024
LOCAL_SAM_CHECKPOINT_BYTES = local_sam_runtime.CHECKPOINT_BYTES


def local_sam_status(hardware: dict | None = None) -> dict:
    hardware = hardware or detect_hardware()
    supported_platform = hardware.get("platform") == "Windows" and str(
        hardware.get("machine", "")
    ).lower() in {"amd64", "x86_64"}
    eligible_gpu = next(
        (
            gpu
            for gpu in hardware.get("gpus", [])
            if int(gpu.get("memory_mib", 0)) >= LOCAL_SAM_MIN_VRAM_MIB
        ),
        None,
    )
    runtime = local_sam_runtime.status()
    disk_eligible = int(hardware.get("disk_free_mib", 0)) >= LOCAL_SAM_MIN_DISK_MIB
    installed = runtime["installed"]
    ready = runtime["ready"]

    if not supported_platform:
        reason = "Local SAM runtime 当前只支持 Windows x86_64"
    elif eligible_gpu is None:
        reason = "未检测到至少 6 GiB VRAM 的 NVIDIA GPU"
    elif not disk_eligible and not installed:
        reason = "安装 Local SAM 至少需要 12 GiB 可用磁盘空间"
    elif runtime["installing"]:
        reason = "Local SAM 正在安装"
    elif not runtime["runtime_installed"]:
        reason = "Local SAM runtime 尚未安装"
    elif not runtime["checkpoint_installed"]:
        reason = "SAM 3.1 checkpoint 尚未同步"
    elif not ready:
        reason = "Local SAM 已安装，首次使用时将启动并加载模型"
    else:
        reason = "Local SAM 已就绪"

    return {
        "available": bool(supported_platform and eligible_gpu is not None and installed),
        "ready": ready,
        "installed": installed,
        "runtime_installed": runtime["runtime_installed"],
        "checkpoint_installed": runtime["checkpoint_installed"],
        "installing": runtime["installing"],
        "state": runtime.get("state", "unknown"),
        "step": runtime.get("step"),
        "error": runtime.get("error"),
        "downloaded_bytes": runtime.get("downloaded_bytes"),
        "hardware_eligible": bool(supported_platform and eligible_gpu is not None),
        "disk_eligible": disk_eligible,
        "min_disk_mib": LOCAL_SAM_MIN_DISK_MIB,
        "supported_platform": supported_platform,
        "reason": reason,
        "min_vram_mib": LOCAL_SAM_MIN_VRAM_MIB,
        "checkpoint_bytes": LOCAL_SAM_CHECKPOINT_BYTES,
        "gpu": eligible_gpu,
        "health": runtime.get("health"),
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
