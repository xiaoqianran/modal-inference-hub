from __future__ import annotations

import os
import socket
from pathlib import Path

import uvicorn


def main() -> None:
    token = os.environ.get("MODAL_3D_AGENT_TOKEN")
    handshake = os.environ.get("MODAL_3D_AGENT_HANDSHAKE")
    if not token or not handshake:
        raise RuntimeError("agent sidecar environment is incomplete")

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(2048)
    port = sock.getsockname()[1]

    path = Path(handshake)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(str(port), encoding="utf-8")
    os.replace(tmp, path)

    config = uvicorn.Config(
        "agent.main:app",
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
