from __future__ import annotations

import os
import socket
import threading
from pathlib import Path

import uvicorn

from agent.main import app
from agent.modal_client import connect


def main() -> None:
    token = os.environ.get("MODAL_3D_AGENT_TOKEN")
    handshake = os.environ.get("MODAL_3D_AGENT_HANDSHAKE")
    if not token or not handshake:
        raise RuntimeError("agent sidecar environment is incomplete")

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(2048)
    port = sock.getsockname()[1]

    saved_token_id = os.environ.pop("MODAL_3D_SAVED_TOKEN_ID", None)
    saved_token_secret = os.environ.pop("MODAL_3D_SAVED_TOKEN_SECRET", None)
    if saved_token_id and saved_token_secret:
        def restore() -> None:
            try:
                connect(saved_token_id, saved_token_secret)
            except Exception:
                pass

        threading.Thread(target=restore, daemon=True).start()

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
