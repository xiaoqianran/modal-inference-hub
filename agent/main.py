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

from agent import artifacts, exports, generation, rembg_preprocess
from agent.capabilities import capabilities
from agent.hardware import detect_hardware
from agent.jobs import jobs
from agent.modal_client import NotConnectedError, connect, connected, disconnect
from agent.models import CapabilityError, public_models, source_input_limits
from agent.projects import projects

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


class ProjectGenerationRequest(BaseModel):
    model: str
    profile: str = "recommended"
    seed: int = 42


class ExportRequest(BaseModel):
    job_id: str


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
    project = projects.save_preprocessed(
        project_id,
        result["matte_bytes"],
        result["canonical_bytes"],
        descriptor,
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
    }


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
        project = projects.get(project_id)
        descriptor, local_path = projects.canonical_local(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="项目不存在") from exc
    except (RuntimeError, FileNotFoundError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        try:
            _, canonical_path = projects.canonical_remote(project_id)
        except RuntimeError:
            uploaded = artifacts.put(local_path.read_bytes(), ".png")
            if uploaded["sha256"] != descriptor["sha256"] or uploaded["bytes"] != descriptor["bytes"]:
                raise artifacts.ArtifactValidationError("Canonical RGBA 上传完整性校验失败")
            canonical_path = uploaded["path"]
            projects.record_remote_canonical(project_id, canonical_path)
        remote = generation.submit(
            request.model,
            canonical_path,
            request.profile,
            request.seed,
        )
    except CapabilityError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    job = jobs.create(remote["model"], remote["call_id"])
    project = projects.record_generation(project_id, request.model, request.profile, job["id"])
    return {"project": project, "job": job}


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
