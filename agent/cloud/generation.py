from __future__ import annotations

import modal

from agent.cloud.registry import GATEWAY_APP, GATEWAY_RESULT_FUNCTION, GATEWAY_SUBMIT_FUNCTION, SUPPORTED_MODELS
from agent.modal_client import require_client


def submit(model: str, input_path: str, options: dict | None = None) -> dict:
    if model not in SUPPORTED_MODELS:
        raise ValueError(f"unsupported model: {model}")
    client = require_client()
    fn = modal.Function.from_name(GATEWAY_APP, GATEWAY_SUBMIT_FUNCTION, client=client)
    return fn.remote(model, input_path, dict(options or {}))


def result(call_id: str) -> dict:
    client = require_client()
    fn = modal.Function.from_name(GATEWAY_APP, GATEWAY_RESULT_FUNCTION, client=client)
    return fn.remote(call_id)
