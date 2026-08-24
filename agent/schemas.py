from __future__ import annotations

from pydantic import BaseModel, Field


class RefineBox(BaseModel):
    cx: float = Field(ge=0.0, le=1.0)
    cy: float = Field(ge=0.0, le=1.0)
    width: float = Field(gt=0.0, le=1.0)
    height: float = Field(gt=0.0, le=1.0)
    positive: bool = True


class SamRefineRequest(BaseModel):
    scene_id: str
    concept: str = Field(min_length=1, max_length=160)
    boxes: list[RefineBox] = Field(min_length=1, max_length=16)
    max_candidates: int = Field(default=16, ge=1, le=24)


class SamMaterializeRequest(BaseModel):
    scene_id: str
    selection_id: str
    candidate_id: str
    output_size: int = Field(default=1024, ge=256, le=2048)


class GenerationRequest(BaseModel):
    model: str
    input_path: str
    options: dict = Field(default_factory=dict)
