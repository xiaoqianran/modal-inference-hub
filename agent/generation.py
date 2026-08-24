from __future__ import annotations

import modal

from agent.modal_client import client
from agent.models import APP_NAME, SUBMIT_FUNCTION, IncompatibleCapability, options_for


def _validate_submission(value, expected_model: str) -> dict:
    if not isinstance(value, dict):
        raise IncompatibleCapability("gateway submission must be an object")
    if value.get("model") != expected_model or value.get("status") != "running":
        raise IncompatibleCapability("gateway returned an invalid submission state")
    task_id = value.get("task_id")
    call_id = value.get("call_id")
    if not isinstance(task_id, str) or not task_id or call_id != task_id:
        raise IncompatibleCapability("gateway returned an invalid task id")
    return value


def submit(
    model: str,
    input_path: str,
    profile: str = "recommended",
    seed: int = 42,
) -> dict:
    fn = modal.Function.from_name(APP_NAME, SUBMIT_FUNCTION, client=client())
    value = fn.remote(model, input_path, options_for(model, profile, seed))
    return _validate_submission(value, model)
