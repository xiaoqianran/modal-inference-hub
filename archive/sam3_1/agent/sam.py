from __future__ import annotations

import modal

from agent.constants import SAM_APP
from agent.modal_client import client

APP_NAME = SAM_APP


def _model():
    return modal.Cls.from_name(APP_NAME, "Model", client=client())()


def segment(image: bytes, concept: str, max_candidates: int = 8) -> dict:
    return _model().segment.remote(image, concept.strip(), max_candidates)


def refine(scene_id: str, concept: str, boxes: list[dict], max_candidates: int = 8) -> dict:
    return _model().refine.remote(scene_id, concept.strip(), boxes, max_candidates)


def materialize(
    scene_id: str,
    selection_id: str,
    candidate_id: str,
    output_size: int = 1024,
) -> dict:
    fn = modal.Function.from_name(APP_NAME, "materialize", client=client())
    return fn.remote(scene_id, selection_id, candidate_id, output_size, False)
