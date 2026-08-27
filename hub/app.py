"""Composition root and HTTP shell for the modular monolith."""

from __future__ import annotations

import hmac
import os
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Query, Request
from fastapi import Path as ApiPath
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .batches import (
    BatchConflict,
    BatchError,
    BatchNotFound,
    BatchService,
    BatchStore,
)
from .deployments import (
    DeploymentConflict,
    DeploymentError,
    DeploymentNotFound,
    DeploymentService,
    DeploymentStore,
    default_deployers,
)
from .direct_images import (
    MAX_INPUT_BYTES,
    DirectImageConflict,
    DirectImageError,
    DirectImageNotFound,
    DirectImageService,
    DirectImageStore,
    InputStore,
)
from .experiments import (
    ExperimentConflict,
    ExperimentError,
    ExperimentNotFound,
    ExperimentService,
    ExperimentStore,
)
from .sidecars import SidecarClient, SidecarConfig, SidecarError


class CreateExperiment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=1, max_length=4000)
    candidate_count: int = Field(default=4, ge=1, le=8)
    image_model: str = Field(min_length=1, max_length=160)
    seed: int = Field(default=42, ge=0, le=2**32 - 8)


class SelectCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str = Field(pattern=r"^candidate-[1-8]$")


class GenerateAsset3D(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(min_length=1, max_length=160)
    profile: str = Field(default="recommended", min_length=1, max_length=160)
    seed: int = Field(default=42, ge=0, le=2**32 - 1)


class ProviderCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token_id: str = Field(min_length=1)
    token_secret: SecretStr


class CreatePromptBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompts: list[str] = Field(min_length=1, max_length=50)
    candidate_count: int = Field(default=4, ge=1, le=8)
    image_model: str = Field(min_length=1, max_length=160)
    seed: int = Field(default=42, ge=0, le=2**32 - 1)


class CreateImageBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sources: list[dict[str, Any]] = Field(min_length=1, max_length=50)
    model: str = Field(min_length=1, max_length=160)
    profile: str = Field(default="recommended", min_length=1, max_length=160)
    seed: int = Field(default=42, ge=0, le=2**32 - 1)


def _service_from_env() -> ExperimentService:
    root = Path(os.environ.get("MODAL_HUB_DATA_DIR", Path.cwd() / ".data"))
    image = SidecarClient(
        SidecarConfig(
            "modal-2d",
            os.environ.get("MODAL_2D_CLIENT_URL", "http://127.0.0.1:3212"),
            "X-Modal-2D-Session",
            os.environ.get("MODAL_2D_CLIENT_TOKEN"),
        )
    )
    asset3d = SidecarClient(
        SidecarConfig(
            "modal-3d",
            os.environ.get("MODAL_3D_CLIENT_URL", "http://127.0.0.1:3213"),
            "X-Modal-3D-Session",
            os.environ.get("MODAL_3D_CLIENT_TOKEN"),
        )
    )
    return ExperimentService(ExperimentStore(root / "experiments.sqlite3"), image, asset3d)


def create_app(
    service: ExperimentService | None = None,
    deployment_service: DeploymentService | None = None,
    direct_image_service: DirectImageService | None = None,
    batch_service: BatchService | None = None,
) -> FastAPI:
    app = FastAPI(title="Modal Inference Hub", version="1.0.0", docs_url=None, redoc_url=None)
    workflow = service or _service_from_env()
    deployments = deployment_service or DeploymentService(
        DeploymentStore(workflow.store.path.parent / "deployments.sqlite3"), default_deployers()
    )
    direct_images = direct_image_service or DirectImageService(
        DirectImageStore(workflow.store.path.parent / "direct-images.sqlite3"),
        InputStore(workflow.store.path.parent / "inputs"),
        workflow.asset3d,
    )
    batches = batch_service or BatchService(
        BatchStore(workflow.store.path.parent / "batches.sqlite3"), workflow, direct_images
    )
    app.state.workflow = workflow
    app.state.deployments = deployments
    app.state.direct_images = direct_images
    app.state.batches = batches
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:1420",
            "http://127.0.0.1:1420",
            "http://tauri.localhost",
            "https://tauri.localhost",
            "tauri://localhost",
        ],
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-File-Name", "X-Modal-Hub-Session"],
    )

    @app.middleware("http")
    async def local_session(request: Request, call_next):
        expected = os.environ.get("MODAL_HUB_SESSION_TOKEN")
        if expected and request.method != "OPTIONS":
            provided = request.headers.get("X-Modal-Hub-Session", "")
            if not hmac.compare_digest(provided, expected):
                return JSONResponse(status_code=401, content={"detail": "invalid local session"})
        return await call_next(request)

    @app.exception_handler(ExperimentNotFound)
    async def not_found(_request: Request, exc: ExperimentNotFound):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ExperimentConflict)
    async def conflict(_request: Request, exc: ExperimentConflict):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ExperimentError)
    async def invalid(_request: Request, exc: ExperimentError):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(SidecarError)
    async def sidecar_failure(_request: Request, exc: SidecarError):
        return JSONResponse(
            status_code=exc.status, content={"detail": str(exc), "provider": exc.provider}
        )

    @app.exception_handler(DeploymentNotFound)
    async def deployment_not_found(_request: Request, exc: DeploymentNotFound):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(DeploymentConflict)
    async def deployment_conflict(_request: Request, exc: DeploymentConflict):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(DeploymentError)
    async def deployment_failure(_request: Request, exc: DeploymentError):
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(DirectImageNotFound)
    async def direct_image_not_found(_request: Request, exc: DirectImageNotFound):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(DirectImageConflict)
    async def direct_image_conflict(_request: Request, exc: DirectImageConflict):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(DirectImageError)
    async def direct_image_invalid(_request: Request, exc: DirectImageError):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(BatchNotFound)
    async def batch_not_found(_request: Request, exc: BatchNotFound):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(BatchConflict)
    async def batch_conflict(_request: Request, exc: BatchConflict):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(BatchError)
    async def batch_invalid(_request: Request, exc: BatchError):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/providers")
    def providers() -> dict[str, Any]:
        result = []
        for client in (workflow.image, workflow.asset3d):
            try:
                health_value = client.health()
                models = client.models() if health_value.get("modal_connected") else []
                result.append(
                    {
                        "id": client.config.provider,
                        "reachable": True,
                        "connected": bool(health_value.get("modal_connected")),
                        "models": models,
                    }
                )
            except SidecarError as exc:
                result.append(
                    {
                        "id": client.config.provider,
                        "reachable": False,
                        "connected": False,
                        "models": [],
                        "error": str(exc),
                    }
                )
        return {"providers": result}

    def provider(provider_id: str) -> SidecarClient:
        clients = {
            workflow.image.config.provider: workflow.image,
            workflow.asset3d.config.provider: workflow.asset3d,
        }
        try:
            return clients[provider_id]
        except KeyError as exc:
            raise ExperimentNotFound("provider not found") from exc

    @app.post("/api/providers/{provider_id}/connection")
    def connect_provider(provider_id: str, body: ProviderCredentials):
        return provider(provider_id).connect(
            body.token_id,
            body.token_secret.get_secret_value(),
        )

    @app.delete("/api/providers/{provider_id}/connection")
    def disconnect_provider(provider_id: str):
        return provider(provider_id).disconnect()

    @app.get("/api/providers/{provider_id}/deployment-plan")
    def deployment_plan(provider_id: str):
        return deployments.plan(provider_id)

    @app.post("/api/providers/{provider_id}/deployments", status_code=202)
    def start_deployment(provider_id: str, body: ProviderCredentials):
        return deployments.start(
            provider_id,
            body.token_id,
            body.token_secret.get_secret_value(),
        )

    @app.get("/api/deployments")
    def list_deployments(limit: int = 50):
        return {"deployments": deployments.list(limit)}

    @app.get("/api/deployments/{deployment_id}")
    def get_deployment(deployment_id: str):
        return deployments.get(deployment_id)

    @app.get("/api/direct-images")
    def list_direct_images(limit: int = 100):
        return {"runs": direct_images.store.list(limit)}

    @app.post("/api/inputs/images", status_code=201)
    async def ingest_image(request: Request):
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > MAX_INPUT_BYTES:
                raise DirectImageError("image exceeds 25 MiB")
        return direct_images.ingest(
            bytes(data), request.headers.get("X-File-Name", "image")
        )

    @app.post("/api/direct-images", status_code=201)
    async def create_direct_image(
        request: Request,
        model: Annotated[str, Query(min_length=1, max_length=160)],
        profile: Annotated[str, Query(min_length=1, max_length=160)] = "recommended",
        seed: Annotated[int, Query(ge=0, le=2**32 - 1)] = 42,
    ):
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > MAX_INPUT_BYTES:
                raise DirectImageError("image exceeds 25 MiB")
        source = direct_images.ingest(
            bytes(data), request.headers.get("X-File-Name", "image")
        )
        return direct_images.create(source, model=model, profile=profile, seed=seed)

    @app.get("/api/direct-images/{run_id}")
    def get_direct_image(run_id: str):
        return direct_images.get(run_id)

    @app.post("/api/direct-images/{run_id}/resume")
    def resume_direct_image(run_id: str):
        return direct_images.resume(run_id)

    @app.get("/api/direct-images/{run_id}/artifact")
    def direct_image_artifact(run_id: str):
        stream, descriptor = direct_images.artifact(run_id)
        digest = descriptor.get("digest") if isinstance(descriptor, dict) else None
        return StreamingResponse(
            stream,
            media_type="model/gltf-binary",
            headers={
                "Content-Disposition": f'attachment; filename="{run_id}.glb"',
                **({"ETag": f'"{digest}"'} if isinstance(digest, str) else {}),
            },
        )

    @app.get("/api/batches")
    def list_batches(limit: int = 50):
        return {"batches": batches.list(limit)}

    @app.post("/api/batches/prompts", status_code=202)
    def create_prompt_batch(body: CreatePromptBatch):
        return batches.create_prompts(body.model_dump())

    @app.post("/api/batches/images", status_code=202)
    def create_image_batch(body: CreateImageBatch):
        return batches.create_images(
            body.sources,
            model=body.model,
            profile=body.profile,
            seed=body.seed,
        )

    @app.get("/api/batches/{batch_id}")
    def get_batch(batch_id: str):
        return batches.get(batch_id)

    @app.post("/api/batches/{batch_id}/resume")
    def resume_batch(batch_id: str):
        return batches.resume(batch_id)

    @app.get("/api/experiments")
    def list_experiments(limit: int = 100):
        return {"experiments": workflow.store.list(limit)}

    @app.post("/api/experiments", status_code=201)
    def create_experiment(body: CreateExperiment):
        return workflow.create(body.model_dump())

    @app.get("/api/experiments/{experiment_id}")
    def get_experiment(experiment_id: Annotated[str, ApiPath(pattern=r"^[A-Za-z0-9_-]+$")]):
        return workflow.get(experiment_id)

    @app.post("/api/experiments/{experiment_id}/selection")
    def choose_candidate(experiment_id: str, body: SelectCandidate):
        return workflow.select(experiment_id, body.candidate_id)

    @app.post("/api/experiments/{experiment_id}/asset3d")
    def generate_asset3d(experiment_id: str, body: GenerateAsset3D):
        return workflow.generate_asset3d(experiment_id, body.model_dump())

    @app.post("/api/experiments/{experiment_id}/resume")
    def resume_experiment(experiment_id: str):
        return workflow.resume(experiment_id)

    @app.delete("/api/experiments/{experiment_id}/active-jobs")
    def cancel_experiment(experiment_id: str):
        return workflow.cancel(experiment_id)

    @app.get("/api/experiments/{experiment_id}/candidates/{candidate_id}/artifact")
    def candidate_artifact(experiment_id: str, candidate_id: str):
        data, headers = workflow.candidate_artifact(experiment_id, candidate_id)
        return Response(
            data,
            media_type="image/png",
            headers={
                key: value
                for key, value in headers.items()
                if key in {"etag", "x-artifact-id", "x-artifact-sha256"}
            },
        )

    @app.get("/api/experiments/{experiment_id}/artifact")
    def output_artifact(experiment_id: str):
        stream, descriptor = workflow.output_artifact(experiment_id)
        digest = descriptor.get("digest") if isinstance(descriptor, dict) else None
        return StreamingResponse(
            stream,
            media_type="model/gltf-binary",
            headers={
                "Content-Disposition": f'attachment; filename="{experiment_id}.glb"',
                **({"ETag": f'"{digest}"'} if isinstance(digest, str) else {}),
            },
        )

    return app


app = create_app()
