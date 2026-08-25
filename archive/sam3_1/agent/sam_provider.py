from __future__ import annotations

from pathlib import Path

from agent import artifacts, local_sam_runtime, sam
from agent.capabilities import capabilities
from agent.modal_client import connected
from agent.settings import get_settings


class SamProviderUnavailable(RuntimeError):
    pass


# SAM Provider 决策表（resolve 依据 sam_mode × 可用性决定实际 provider）：
#
#   sam_mode │ Local 可用 │ Cloud 可用 │ 结果
#   ─────────┼────────────┼────────────┼──────────────────────────
#   auto     │    是      │   任意     │ local（优先本地）
#   auto     │    否      │    是      │ cloud（回退云端）
#   auto     │    否      │    否      │ 抛 SamProviderUnavailable
#   local    │    是      │   任意     │ local
#   local    │    否      │   任意     │ 抛 SamProviderUnavailable(原因)
#   cloud    │   任意     │    是      │ cloud
#   cloud    │   任意     │    否      │ 抛 SamProviderUnavailable
#
# 注意：segment() 在 auto 模式下，若 Local 调用失败且 Cloud 可用，会再回落一次 Cloud；
# materialize()/refine() 则严格按项目持久化的 provider 执行，不再跨 provider 回退。


def resolve() -> str:
    state = capabilities()["sam"]          # 获取 SAM 能力状态
    mode = get_settings()["sam_mode"]      # 用户配置：auto/local/cloud

    effective = state["effective"]         # 云端推荐的有效 provider
    if effective:
        return effective                    # 云端优先

    # effective 为空时，根据模式降级
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
        value = sam.materialize(scene_id, selection_id, candidate_id, output_size)
        descriptor = artifacts.describe_remote_png(
            value["canonical_path"], value.get("canonical_bytes")
        )
        expected_sha = value.get("canonical_sha256")
        expected_id = value.get("canonical_id")
        if expected_sha is not None and expected_sha != descriptor["sha256"]:
            raise artifacts.ArtifactValidationError("canonical SHA-256 校验失败")
        if expected_id is not None and expected_id != descriptor["id"]:
            raise artifacts.ArtifactValidationError("canonical ID 校验失败")
        return {
            **value,
            "canonical_path": descriptor["path"],
            "canonical_id": descriptor["id"],
            "canonical_sha256": descriptor["sha256"],
            "canonical_bytes": descriptor["bytes"],
        }
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
        "canonical_id": artifacts.content_id("can", "canonical", uploaded["sha256"]),
        "canonical_sha256": uploaded["sha256"],
        "canonical_bytes": uploaded["bytes"],
        "local_canonical_bytes": local["canonical_bytes"],
        "canonical": local["canonical"],
    }
