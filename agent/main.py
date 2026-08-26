from __future__ import annotations

import hmac
import os
import time
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.background import BackgroundTask
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

from agent import artifacts, exports, rembg_preprocess
from agent.capabilities import capabilities
from agent.connector.api import create_router
from agent.connector.service import connector_allowed_origins
from agent.generation_service import GenerationCoordinator, GenerationRecoveryPending
from agent.generation_store import (
    GenerationConflict,
    GenerationIntentStore,
    GenerationSubmissionUnknown,
)
from agent.hardware import detect_hardware
from agent.jobs import jobs
from agent.modal_client import NotConnectedError, connect, connected, disconnect
from agent.models import CapabilityError, public_models, source_input_limits
from agent.projects import projects
from agent.statuses import PROJECT_REMOTE_ACTIVE_STATUSES

generation_intents = GenerationIntentStore(projects.db_path)
generation_coordinator = GenerationCoordinator(projects, generation_intents, jobs)


def recover_generation_state() -> None:
    generation_coordinator.recover_after_restart()

app = FastAPI(title="modal-3D 本地代理", docs_url=None, redoc_url=None)
_tauri_origins = (
    "http://localhost:1420",
    "http://127.0.0.1:1420",
    "http://tauri.localhost",
    "https://tauri.localhost",
    "tauri://localhost",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(dict.fromkeys((*_tauri_origins, *connector_allowed_origins()))),
    allow_methods=["*"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Origin",
        "X-Connector-Pairing",
        "X-Modal-3D-Session",
    ],
)


@app.middleware("http")
async def request_diagnostics(request: Request, call_next):
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        print(
            f"[agent] request failed method={request.method} path={request.url.path} "
            f"elapsed_ms={elapsed_ms} type={type(exc).__name__}",
            flush=True,
        )
        raise
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    if response.status_code >= 400 or elapsed_ms >= 2000:
        print(
            f"[agent] request method={request.method} path={request.url.path} "
            f"status={response.status_code} elapsed_ms={elapsed_ms}",
            flush=True,
        )
    return response


@app.middleware("http")
async def require_session(request: Request, call_next):
    if request.url.path.startswith("/connector/v1/"):
        return await call_next(request)
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


class PreprocessProviderRequest(BaseModel):
    provider: str


class ProjectComponentSelectionRequest(BaseModel):
    selected_component_ids: list[str]


class ProjectGenerationRequest(BaseModel):
    request_id: str = Field(min_length=16, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    model: str
    profile: str = "recommended"
    seed: int = 42


class ExportRequest(BaseModel):
    job_id: str


app.include_router(create_router())

@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/hardware")
def hardware() -> dict:
    return detect_hardware()


@app.get("/v1/capabilities")
def runtime_capabilities() -> dict:
    return capabilities()


@app.get("/v1/preprocess/status")
def preprocess_status() -> dict:
    return rembg_preprocess.status()


@app.post("/v1/preprocess/provider")
def preprocess_provider(request: PreprocessProviderRequest) -> dict:
    try:
        return rembg_preprocess.set_provider_preference(request.provider)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/preprocess/model")
def preprocess_prepare_model() -> dict:
    return rembg_preprocess.prepare_model_async()


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
        limits = source_input_limits()
        return projects.create(await file.read(), file.filename or "source.png", limits)
    except CapabilityError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except artifacts.ArtifactValidationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
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


@app.get("/v1/projects/{project_id}/generations")
def project_generations(project_id: str) -> list[dict]:
    try:
        return projects.list_generations(project_id)
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


@app.post("/v1/projects/{project_id}/preprocess")
def project_preprocess(project_id: str) -> dict:
    try:
        project = projects.get(project_id)
        if project["status"] in PROJECT_REMOTE_ACTIVE_STATUSES:
            raise HTTPException(status_code=409, detail="远程生成任务活动期间不能重新执行 rembg")
        source = projects.source_bytes(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="项目不存在") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=410, detail="项目源图片已丢失") from exc
    try:
        result = rembg_preprocess.process(source)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    descriptor = {
        "id": artifacts.content_id("can", "canonical", result["canonical_sha256"]),
        "role": "canonical-rgba",
        "mime": "image/png",
        "bytes": len(result["canonical_bytes"]),
        "sha256": result["canonical_sha256"],
        "width": 1024,
        "height": 1024,
        "mode": "RGBA",
    }
    component_state = {
        "source_size": result["source_size"],
        "components": result["components"],
        "selected_component_ids": result["selected_component_ids"],
        "component_count": result["component_count"],
        "raw_component_count": result["raw_component_count"],
        "ignored_component_count": result["ignored_component_count"],
        "ignored_foreground_pixels": result["ignored_foreground_pixels"],
        "minimum_component_pixels": result["minimum_component_pixels"],
    }
    project = projects.save_preprocessed(
        project_id,
        result["matte_bytes"],
        result["canonical_bytes"],
        descriptor,
        component_state,
    )
    metrics = {
        key: value
        for key, value in result.items()
        if key not in {"matte_bytes", "canonical_bytes", "matte_sha256", "canonical_sha256"}
    }
    return {
        "project": project,
        "canonical": descriptor,
        "matte": {
            "mime": "image/png",
            "bytes": len(result["matte_bytes"]),
            "sha256": result["matte_sha256"],
        },
        "preprocess": metrics,
        "component_state": component_state,
    }


@app.get("/v1/projects/{project_id}/components")
def project_components(project_id: str) -> dict:
    try:
        state = projects.component_state(project_id)
        descriptor = projects.canonical_descriptor(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="项目不存在") from exc
    except (FileNotFoundError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail="项目尚未完成本地抠图") from exc
    return {"component_state": state, "canonical": descriptor}


@app.post("/v1/projects/{project_id}/components")
def project_component_selection(project_id: str, request: ProjectComponentSelectionRequest) -> dict:
    try:
        current = projects.get(project_id)
        if current["status"] in PROJECT_REMOTE_ACTIVE_STATUSES:
            raise HTTPException(status_code=409, detail="远程生成任务活动期间不能修改前景选择")
        matte_bytes = projects.matte_path(project_id).read_bytes()
        component_state = projects.component_state(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="项目不存在") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail="项目尚未完成本地抠图") from exc
    try:
        result = rembg_preprocess.canonicalize_components(
            matte_bytes,
            request.selected_component_ids,
            component_state=component_state,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    descriptor = {
        "id": artifacts.content_id("can", "canonical", result["canonical_sha256"]),
        "role": "canonical-rgba",
        "mime": "image/png",
        "bytes": len(result["canonical_bytes"]),
        "sha256": result["canonical_sha256"],
        "width": 1024,
        "height": 1024,
        "mode": "RGBA",
    }
    state = {
        "source_size": result["source_size"],
        "components": result["components"],
        "selected_component_ids": result["selected_component_ids"],
        "component_count": result["component_count"],
        "raw_component_count": result["raw_component_count"],
        "ignored_component_count": result["ignored_component_count"],
        "ignored_foreground_pixels": result["ignored_foreground_pixels"],
        "minimum_component_pixels": result["minimum_component_pixels"],
        "foreground_bbox": result["foreground_bbox"],
        "selection_elapsed_ms": result["selection_elapsed_ms"],
    }
    project = projects.save_canonical_selection(
        project_id,
        result["selection_bytes"],
        result["canonical_bytes"],
        descriptor,
        state,
    )
    return {"project": project, "canonical": descriptor, "component_state": state}


@app.get("/v1/projects/{project_id}/selection")
def project_selection(project_id: str):
    try:
        path = projects.selection_path(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="项目不存在") from exc
    except FileNotFoundError:
        try:
            matte_bytes = projects.matte_path(project_id).read_bytes()
            state = projects.component_state(project_id)
            selected = state.get("selected_component_ids") or [
                item["id"] for item in state.get("components", [])
            ]
            result = rembg_preprocess.canonicalize_components(
                matte_bytes,
                selected,
                component_state=state,
            )
            path = projects.save_selection_preview(project_id, result["selection_bytes"])
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="项目不存在") from exc
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=409, detail="项目尚无可恢复的前景选择预览") from exc
    return FileResponse(path, media_type="image/png")


@app.get("/v1/projects/{project_id}/matte")
def project_matte(project_id: str):
    try:
        path = projects.matte_path(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="项目不存在") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail="项目尚未完成本地抠图") from exc
    return FileResponse(path, media_type="image/png")


@app.get("/v1/projects/{project_id}/canonical")
def project_canonical(project_id: str):
    try:
        descriptor, path = projects.canonical_local(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="项目不存在") from exc
    except (RuntimeError, FileNotFoundError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return FileResponse(
        path,
        media_type=descriptor["mime"],
        headers={"ETag": f'"{descriptor["sha256"]}"'},
    )


@app.post("/v1/projects/{project_id}/generation")
def project_generation(project_id: str, request: ProjectGenerationRequest) -> dict:
    try:
        return generation_coordinator.submit(
            project_id,
            request.request_id,
            request.model,
            request.profile,
            request.seed,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="项目不存在") from exc
    except (GenerationSubmissionUnknown, GenerationConflict) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except GenerationRecoveryPending as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except CapabilityError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except artifacts.ArtifactValidationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except (RuntimeError, FileNotFoundError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/v1/projects/{project_id}/generation/abandon-unknown")
def abandon_unknown_generation(project_id: str) -> dict:
    try:
        generation_intents.abandon_uncertain(project_id)
        return projects.get(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="项目不存在") from exc
    except GenerationConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/v1/exports")
def export_prepare(request: ExportRequest) -> dict:
    try:
        descriptor, path = jobs.artifact(request.job_id)
        return exports.prepare(path, descriptor)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="任务不存在") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=410, detail="已验证的本地产物不存在") from exc
    except artifacts.ArtifactValidationError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/v1/jobs/{job_id}/artifact")
def job_artifact(job_id: str):
    try:
        descriptor, path = jobs.artifact(job_id)
        artifacts.lease(path)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="任务不存在") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=410, detail="已验证的本地产物不存在") from exc
    except artifacts.ArtifactValidationError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return FileResponse(
        path,
        media_type=descriptor["mime"],
        filename=f'{descriptor["id"]}.glb',
        headers={"ETag": f'"{descriptor["sha256"]}"'},
        background=BackgroundTask(artifacts.release, path),
    )


@app.get("/v1/assets", deprecated=True)
def download_asset(path: str = Query(...)):
    try:
        normalized = artifacts.normalize_path(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not normalized.startswith("client-inputs/"):
        raise HTTPException(410, "path-based generation artifact access is retired")
    chunks = artifacts.read(normalized)
    media_type = {
        ".png": "image/png",
        ".glb": "model/gltf-binary",
    }.get(Path(normalized).suffix.lower(), "application/octet-stream")
    return StreamingResponse(chunks, media_type=media_type)


@app.get("/v1/models")
def models() -> list[dict]:
    try:
        return public_models()
    except CapabilityError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


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
