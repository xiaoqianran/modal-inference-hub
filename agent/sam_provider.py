from __future__ import annotations

from pathlib import Path

from agent import artifacts, local_sam_runtime, sam
from agent.capabilities import capabilities
from agent.modal_client import connected
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


def segment(image_path: Path, concept: str, max_candidates: int = 8) -> tuple[str, dict]:
    provider = resolve()
    if provider == "local":
        try:
            return provider, local_sam_runtime.request_segment(image_path, concept, max_candidates)
        except RuntimeError as exc:
            if get_settings()["sam_mode"] != "auto" or not connected():
                raise SamProviderUnavailable(str(exc)) from exc
            return "cloud", sam.segment(image_path.read_bytes(), concept, max_candidates)
    return provider, sam.segment(image_path.read_bytes(), concept, max_candidates)


def refine(
    provider: str,
    scene_id: str,
    concept: str,
    boxes: list[dict],
    max_candidates: int = 8,
) -> dict:
    if provider == "local":
        return local_sam_runtime.request_refine(scene_id, concept, boxes, max_candidates)
    if provider == "cloud":
        return sam.refine(scene_id, concept, boxes, max_candidates)
    raise SamProviderUnavailable(f"未知 SAM provider：{provider}")


def materialize(
    provider: str,
    scene_id: str,
    selection_id: str,
    candidate_id: str,
    output_size: int = 1024,
) -> dict:
    if provider == "cloud":
        return sam.materialize(scene_id, selection_id, candidate_id, output_size)
    if provider != "local":
        raise SamProviderUnavailable(f"未知 SAM provider：{provider}")

    local = local_sam_runtime.request_materialize(
        scene_id,
        selection_id,
        candidate_id,
        output_size,
    )
    canonical_file = Path(local["canonical_file"])
    uploaded = artifacts.put(canonical_file.read_bytes(), ".png")
    return {
        "scene_id": scene_id,
        "selection_id": selection_id,
        "candidate_id": candidate_id,
        "canonical_path": uploaded["path"],
        "canonical_bytes": uploaded["bytes"],
        "local_canonical_bytes": local["canonical_bytes"],
        "canonical": local["canonical"],
    }
