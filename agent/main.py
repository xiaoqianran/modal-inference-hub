from __future__ import annotations

import hmac
import os
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from modal.exception import (
    AuthError,
    PermissionDeniedError,
)
from modal.exception import (
    ConnectionError as ModalConnectionError,
)
from modal.exception import (
    TimeoutError as ModalTimeoutError,
)
from pydantic import BaseModel, Field, SecretStr

from agent import artifacts, generation, sam
from agent.hardware import detect_hardware
from agent.jobs import jobs
from agent.modal_client import NotConnectedError, connect, connected, disconnect
from agent.models import public_models

app = FastAPI(title="modal-3D 本地代理", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:1420",
        "http://127.0.0.1:1420",
        "http://tauri.localhost",
        "https://tauri.localhost",
        "tauri://localhost",
    ],
    allow_methods=["*"],
    allow_headers=["Content-Type", "X-Modal-3D-Session"],
)


@app.middleware("http")
async def require_session(request: Request, call_next):
    expected = os.environ.get("MODAL_3D_AGENT_TOKEN")
    if expected and request.method != "OPTIONS":
        provided = request.headers.get("X-Modal-3D-Session", "")
        if not hmac.compare_digest(provided, expected):
            return JSONResponse(status_code=401, content={"detail": "本地会话无效"})
    return await call_next(request)


@app.exception_handler(NotConnectedError)
async def modal_required(_request: Request, exc: NotConnectedError):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


class ModalCredentials(BaseModel):
    token_id: str
    token_secret: SecretStr


class RefineRequest(BaseModel):
    scene_id: str
    concept: str
    boxes: list[dict]
    max_candidates: int = Field(default=8, ge=1, le=16)


class MaterializeRequest(BaseModel):
    scene_id: str
    selection_id: str
    candidate_id: str
    output_size: int = Field(default=1024, ge=256, le=2048)


class GenerationRequest(BaseModel):
    model: str
    input_path: str
    profile: str = "recommended"
    seed: int = 42


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/hardware")
def hardware() -> dict:
    return detect_hardware()


@app.get("/modal/status")
def modal_status() -> dict:
    return {"connected": connected()}


@app.post("/modal/connect")
def modal_connect(credentials: ModalCredentials) -> dict:
    token_id = credentials.token_id.strip()
    token_secret = credentials.token_secret.get_secret_value().strip()
    if not token_id or not token_secret:
        raise HTTPException(status_code=400, detail="令牌 ID 和令牌密钥不能为空")
    try:
        connect(token_id, token_secret)
    except (AuthError, PermissionDeniedError) as exc:
        raise HTTPException(status_code=401, detail="Modal 身份验证失败") from exc
    except (ModalConnectionError, ModalTimeoutError) as exc:
        raise HTTPException(status_code=503, detail="Modal 服务当前不可用") from exc
    return {"ok": True}


@app.delete("/modal/connect")
def modal_disconnect() -> dict:
    disconnect()
    return {"ok": True}


@app.post("/v1/assets")
async def upload_asset(file: Annotated[UploadFile, File()]) -> dict:
    data = await file.read()
    suffix = Path(file.filename or "").suffix or ".bin"
    try:
        return artifacts.put(data, suffix)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/v1/assets")
def download_asset(path: str = Query(...)):
    try:
        chunks = artifacts.read(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    media_type = {
        ".png": "image/png",
        ".glb": "model/gltf-binary",
    }.get(Path(path).suffix.lower(), "application/octet-stream")
    return StreamingResponse(chunks, media_type=media_type)


@app.post("/v1/sam/segment")
async def sam_segment(
    image: Annotated[UploadFile, File()],
    concept: Annotated[str, Form()],
    max_candidates: Annotated[int, Form(ge=1, le=16)] = 8,
) -> dict:
    data = await image.read()
    if not data:
        raise HTTPException(status_code=400, detail="图片为空")
    if not concept.strip():
        raise HTTPException(status_code=400, detail="请输入要提取的对象")
    return sam.segment(data, concept, max_candidates)


@app.post("/v1/sam/refine")
def sam_refine(request: RefineRequest) -> dict:
    return sam.refine(request.scene_id, request.concept, request.boxes, request.max_candidates)


@app.post("/v1/sam/materialize")
def sam_materialize(request: MaterializeRequest) -> dict:
    return sam.materialize(
        request.scene_id,
        request.selection_id,
        request.candidate_id,
        request.output_size,
    )


@app.get("/v1/models")
def models() -> list[dict]:
    return public_models()


@app.post("/v1/generations")
def generation_submit(request: GenerationRequest) -> dict:
    try:
        remote = generation.submit(
            request.model,
            request.input_path,
            request.profile,
            request.seed,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return jobs.create(remote["model"], remote["call_id"])


@app.get("/v1/jobs")
def job_list() -> list[dict]:
    return jobs.list()


@app.get("/v1/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    try:
        return jobs.poll(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="任务不存在") from exc


@app.delete("/v1/jobs/{job_id}")
def job_cancel(job_id: str) -> dict:
    try:
        return jobs.cancel(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="任务不存在") from exc
