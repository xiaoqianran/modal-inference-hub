"""Desktop process shell. Business behavior lives in :mod:`hub`."""

from __future__ import annotations

import os
import socket
import sys
import threading
from pathlib import Path

import uvicorn

from hub.app import app


def _watch_parent(parent_pid: int) -> None:
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(0x00100000, False, parent_pid)
    if not handle:
        os._exit(0)
    kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)
    kernel32.CloseHandle(handle)
    os._exit(0)


def main() -> None:
    token = os.environ.get("MODAL_HUB_SESSION_TOKEN")
    handshake = os.environ.get("MODAL_HUB_HANDSHAKE")
    if not token or not handshake:
        raise RuntimeError("hub desktop startup environment is incomplete")

    parent = os.environ.get("MODAL_HUB_PARENT_PID")
    if parent and parent.isdigit() and sys.platform == "win32":
        threading.Thread(target=_watch_parent, args=(int(parent),), daemon=True).start()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    port = sock.getsockname()[1]
    path = Path(handshake)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(str(port), encoding="utf-8")
    os.replace(temporary, path)

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    try:
        uvicorn.Server(config).run(sockets=[sock])
    finally:
        path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
