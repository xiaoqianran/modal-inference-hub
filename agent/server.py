from __future__ import annotations

import os
import socket
import sys
import threading
from pathlib import Path

import uvicorn

from agent.main import app


def _watch_windows_parent(parent_pid: int) -> None:
    """桌面主进程崩溃或被强制关闭时，自动停止本地代理。"""
    import ctypes
    from ctypes import wintypes

    synchronize = 0x00100000
    infinite = 0xFFFFFFFF
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(synchronize, False, parent_pid)
    if not handle:
        return
    try:
        kernel32.WaitForSingleObject(handle, infinite)
    finally:
        kernel32.CloseHandle(handle)
    os._exit(0)


def _start_parent_watchdog() -> None:
    if sys.platform != "win32":
        return
    value = os.environ.get("MODAL_3D_AGENT_PARENT_PID")
    if not value:
        return
    try:
        parent_pid = int(value)
    except ValueError:
        return
    threading.Thread(
        target=_watch_windows_parent,
        args=(parent_pid,),
        name="desktop-parent-watchdog",
        daemon=True,
    ).start()


def main() -> None:
    token = os.environ.get("MODAL_3D_AGENT_TOKEN")
    handshake = os.environ.get("MODAL_3D_AGENT_HANDSHAKE")
    if not token or not handshake:
        raise RuntimeError("本地代理 sidecar 的启动环境不完整")

    _start_parent_watchdog()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(2048)
    port = sock.getsockname()[1]

    path = Path(handshake)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(str(port), encoding="utf-8")
    os.replace(tmp, path)

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
    )
    try:
        uvicorn.Server(config).run(sockets=[sock])
    finally:
        path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
