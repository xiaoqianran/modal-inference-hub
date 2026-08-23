from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Profile:
    id: str
    name: str
    options: dict


@dataclass(frozen=True)
class ModelSpec:
    id: str
    name: str
    description: str
    output: str
    warm_seconds: float
    profiles: tuple[Profile, ...]

    def public(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "output": self.output,
            "warm_seconds": self.warm_seconds,
            "profiles": [{"id": profile.id, "name": profile.name} for profile in self.profiles],
        }


MODELS = (
    ModelSpec(
        id="fastsam3d-plus-plus",
        name="FastSAM3D++",
        description="最快的几何生成；vertex-color GLB",
        output="geometry",
        warm_seconds=6.06,
        profiles=(Profile("recommended", "推荐 · 已验证", {"dmd_interval": 1, "dmd_history": 5}),),
    ),
    ModelSpec(
        id="hermit-trellis2-plus-plus",
        name="Hermite-TRELLIS2++",
        description="1024 cascade 几何；Hermite / DMD",
        output="geometry",
        warm_seconds=11.98,
        profiles=(Profile("recommended", "推荐 · 已验证", {}),),
    ),
    ModelSpec(
        id="hunyuan2.1-plus-plus",
        name="Hunyuan2.1++",
        description="平衡几何；HiCache++ DMD",
        output="geometry",
        warm_seconds=29.56,
        profiles=(
            Profile(
                "recommended",
                "推荐 · 已验证",
                {"interval": 3, "history": 6, "num_inference_steps": 50},
            ),
        ),
    ),
    ModelSpec(
        id="pixal3d",
        name="Pixal3D",
        description="完整纹理 GLB；1024 cascade + 4096 texture",
        output="textured",
        warm_seconds=108.92,
        profiles=(Profile("recommended", "推荐 · 已验证", {"fov": None}),),
    ),
)

_BY_ID = {model.id: model for model in MODELS}


def public_models() -> list[dict]:
    return [model.public() for model in MODELS]


def options_for(model_id: str, profile_id: str, seed: int) -> dict:
    try:
        model = _BY_ID[model_id]
    except KeyError as exc:
        raise ValueError(f"未知模型：{model_id}") from exc
    profile = next((item for item in model.profiles if item.id == profile_id), None)
    if profile is None:
        raise ValueError(f"模型 {model_id} 不支持 profile：{profile_id}")
    return {"seed": seed, **profile.options}
