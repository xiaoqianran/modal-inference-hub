from __future__ import annotations

from agent import sam
from agent.capabilities import capabilities
from agent.settings import get_settings


class SamProviderUnavailable(RuntimeError):
    pass


def resolve() -> str:
    state = capabilities()["sam"]
    mode = get_settings()["sam_mode"]
    effective = state["effective"]
    if effective:
        return effective
    if mode == "local":
        raise SamProviderUnavailable(state["local"]["reason"])
    if mode == "cloud":
        raise SamProviderUnavailable("Modal 尚未连接，Cloud SAM 不可用")
    raise SamProviderUnavailable("当前没有可用的 SAM Provider")


def segment(image: bytes, concept: str, max_candidates: int = 8) -> tuple[str, dict]:
    provider = resolve()
    if provider == "cloud":
        return provider, sam.segment(image, concept, max_candidates)
    raise SamProviderUnavailable("本地 SAM runtime 尚未启用")




def refine(
    provider: str,
    scene_id: str,
    concept: str,
    boxes: list[dict],
    max_candidates: int = 8,
) -> dict:
    if provider == "cloud":
        return sam.refine(scene_id, concept, boxes, max_candidates)
    raise SamProviderUnavailable("本地 SAM runtime 尚未启用")


def materialize(
    provider: str,
    scene_id: str,
    selection_id: str,
    candidate_id: str,
    output_size: int = 1024,
) -> dict:
    if provider == "cloud":
        return sam.materialize(scene_id, selection_id, candidate_id, output_size)
    raise SamProviderUnavailable("本地 SAM runtime 尚未启用")
