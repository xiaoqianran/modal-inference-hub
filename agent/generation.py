from __future__ import annotations

import modal

from agent.modal_client import client
from agent.models import APP_NAME, options_for


def submit(
    model: str,
    input_path: str,
    profile: str = "recommended",
    seed: int = 42,
) -> dict:
    fn = modal.Function.from_name(APP_NAME, "submit", client=client())
    return fn.remote(model, input_path, options_for(model, profile, seed))
