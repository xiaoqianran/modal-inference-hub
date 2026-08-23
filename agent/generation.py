from __future__ import annotations

import modal

from agent.modal_client import client

APP_NAME = "modal-3d-gateway"


def submit(model: str, input_path: str, options: dict | None = None) -> dict:
    fn = modal.Function.from_name(APP_NAME, "submit", client=client())
    return fn.remote(model, input_path, dict(options or {}))
