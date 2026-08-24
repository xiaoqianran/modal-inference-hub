from __future__ import annotations

import modal

from agent.cloud.registry import SAM_APP, SAM_CLASS, SAM_MATERIALIZE_FUNCTION
from agent.modal_client import require_client


def segment(image_bytes: bytes, concept: str, max_candidates: int = 16) -> dict:
    client = require_client()
    cls = modal.Cls.from_name(SAM_APP, SAM_CLASS, client=client)
    return cls().segment.remote(image_bytes, concept, max_candidates)


def refine(scene_id: str, concept: str, boxes: list[dict], max_candidates: int = 16) -> dict:
    client = require_client()
    cls = modal.Cls.from_name(SAM_APP, SAM_CLASS, client=client)
    return cls().refine.remote(scene_id, concept, boxes, max_candidates)


def materialize(scene_id: str, selection_id: str, candidate_id: str, output_size: int = 1024) -> dict:
    client = require_client()
    fn = modal.Function.from_name(SAM_APP, SAM_MATERIALIZE_FUNCTION, client=client)
    return fn.remote(scene_id, selection_id, candidate_id, output_size, False)
