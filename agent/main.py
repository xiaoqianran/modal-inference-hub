from __future__ import annotations

import hmac
import os
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
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

from agent import artifacts, exports, generation, local_sam_runtime, sam, sam_provider
from agent.capabilities import capabilities
from agent.hardware import detect_hardware
from agent.jobs import jobs
from agent.modal_client import NotConnectedError, connect, connected, disconnect
from agent.models import public_models
from agent.projects import projects
from agent.sam_provider import SamProviderUnavailable
from agent.settings import get_settings, set_sam_mode

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


@app.exception_handler(SamProviderUnavailable)
async def sam_provider_required(_request: Request, exc: SamProviderUnavailable):
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


class ProjectSegmentRequest(BaseModel):
    concept: str
    max_candidates: int = Field(default=8, ge=1, le=16)


class ProjectRefineRequest(BaseModel):
    boxes: list[dict] = Field(min_length=1, max_length=16)
    max_candidates: int = Field(default=8, ge=1, le=16)


class ProjectMaterializeRequest(BaseModel):
    candidate_id: str
    output_size: int = Field(default=1024, ge=256, le=2048)


class ProjectGenerationRequest(BaseModel):
    model: str
    profile: str = "recommended"
    seed: int = 42


class SamSettingsRequest(BaseModel):
    mode: str


class ExportRequest(BaseModel):
    artifact_path: str


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/hardware")
def hardware() -> dict:
    return detect_hardware()


@app.get("/v1/capabilities")
def runtime_capabilities() -> dict:
    return capabilities()


@app.get("/v1/local-sam/status")
def local_sam_status() -> dict:
    return local_sam_runtime.status()


@app.post("/v1/local-sam/install")
def local_sam_install() -> dict:
    if not connected():
        raise HTTPException(status_code=409, detail="请先连接 Modal，以同步 SAM 3.1 checkpoint")
    local = capabilities()["sam"]["local"]
    if not local["hardware_eligible"] or not local["disk_eligible"]:
        raise HTTPException(status_code=409, detail=local["reason"])
    return local_sam_runtime.begin_install()


@app.delete("/v1/local-sam/install")
def local_sam_uninstall() -> dict:
    try:
        result = local_sam_runtime.uninstall()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if get_settings()["sam_mode"] == "local":
        set_sam_mode("auto")
    return result


@app.post("/v1/local-sam/start")
def local_sam_start() -> dict:
    try:
        return local_sam_runtime.start()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.delete("/v1/local-sam/start")
def local_sam_stop() -> dict:
    local_sam_runtime.stop()
    return {"ok": True}


@app.get("/v1/settings/sam")
def sam_settings() -> dict:
    return get_settings()


@app.put("/v1/settings/sam")
def sam_settings_update(request: SamSettingsRequest) -> dict:
    try:
        return set_sam_mode(request.mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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




@app.post("/v1/projects")
async def project_create(file: Annotated[UploadFile, File()]) -> dict:
    try:
        return projects.create(await file.read(), file.filename or "source.png")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/v1/projects")
def project_list() -> list[dict]:
    return projects.list()


@app.delete("/v1/projects/{project_id}")
def project_delete(project_id: str) -> dict:
    try:
        deleted = projects.delete(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="项目不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"deleted": deleted["id"]}


@app.get("/v1/projects/{project_id}")
def project_get(project_id: str) -> dict:
    try:
        return projects.get(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="项目不存在") from exc


@app.get("/v1/projects/{project_id}/source")
def project_source(project_id: str):
    try:
        path = projects.source_path(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="项目不存在") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=410, detail="项目源图片已丢失") from exc
    return FileResponse(path)


@app.post("/v1/projects/{project_id}/segment")
def project_segment(project_id: str, request: ProjectSegmentRequest) -> dict:
    concept = request.concept.strip()
    if not concept:
        raise HTTPException(status_code=400, detail="请输入要提取的对象")
    try:
        image_path = projects.source_path(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="项目不存在") from exc
    provider, selection = sam_provider.segment(image_path, concept, request.max_candidates)
    project = projects.record_segmentation(project_id, concept, provider, selection)
    return {"project": project, "selection": selection, "provider": provider}


@app.post("/v1/projects/{project_id}/refine")
def project_refine(project_id: str, request: ProjectRefineRequest) -> dict:
    try:
        project = projects.get(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="项目不存在") from exc
    if not project["scene_id"] or not project["concept"]:
        raise HTTPException(status_code=409, detail="请先完成对象识别")
    provider = project["sam_provider"] or "cloud"
    selection = sam_provider.refine(
        provider,
        project["scene_id"],
        project["concept"],
        request.boxes,
        request.max_candidates,
    )
    project = projects.record_segmentation(project_id, project["concept"], provider, selection)
    return {"project": project, "selection": selection, "provider": provider}


@app.post("/v1/projects/{project_id}/materialize")
def project_materialize(project_id: str, request: ProjectMaterializeRequest) -> dict:
    try:
        project = projects.get(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="项目不存在") from exc
    if not project["scene_id"] or not project["selection_id"]:
        raise HTTPException(status_code=409, detail="请先完成对象识别")
    provider = project["sam_provider"] or "cloud"
    canonical = sam_provider.materialize(
        provider,
        project["scene_id"],
        project["selection_id"],
        request.candidate_id,
        request.output_size,
    )
    project = projects.record_canonical(project_id, request.candidate_id, canonical)
    return {"project": project, "canonical": canonical}


@app.post("/v1/projects/{project_id}/generation")
def project_generation(project_id: str, request: ProjectGenerationRequest) -> dict:
    try:
        project = projects.get(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="项目不存在") from exc
    if not project["canonical_path"]:
        raise HTTPException(status_code=409, detail="请先确认 Canonical RGBA")
    try:
        remote = generation.submit(
            request.model,
            project["canonical_path"],
            request.profile,
            request.seed,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    job = jobs.create(remote["model"], remote["call_id"])
    project = projects.record_generation(project_id, request.model, request.profile, job["id"])
    return {"project": project, "job": job}


@app.post("/v1/exports")
def export_prepare(request: ExportRequest) -> dict:
    try:
        return exports.prepare(request.artifact_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
        job = jobs.poll(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="任务不存在") from exc
    projects.record_job(job)
    return job


@app.delete("/v1/jobs/{job_id}")
def job_cancel(job_id: str) -> dict:
    try:
        job = jobs.cancel(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="任务不存在") from exc
    projects.record_job(job)
    return job
