from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict

from .contracts import ConnectorError
from .service import ConnectorService


class PairRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clientIdentity: str
    contractVersion: str
    origin: str
    scopes: list[str]


def create_router(service: ConnectorService | None = None) -> APIRouter:
    connector = service or ConnectorService()
    router = APIRouter(prefix="/connector/v1")

    def failure(exc: ConnectorError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content={"code": exc.code})

    def session(request: Request, scope: str):
        origin = request.headers.get("Origin", "")
        if not origin:
            raise ConnectorError("CONNECTION_REQUIRED", 403, "Origin header required")
        return connector.authenticate(request.headers.get("Authorization", ""), origin, scope)

    @router.post("/session")
    def pair(request: Request, body: PairRequest):
        try:
            return connector.pair(
                body.model_dump(),
                approval=request.headers.get("X-Connector-Pairing", ""),
                origin_header=request.headers.get("Origin", ""),
            )
        except ConnectorError as exc:
            return failure(exc)

    @router.delete("/session")
    def revoke(request: Request):
        try:
            origin = request.headers.get("Origin", "")
            if not origin:
                raise ConnectorError("CONNECTION_REQUIRED", 403, "Origin header required")
            connector.revoke(request.headers.get("Authorization", ""), origin)
            return {"revoked": True}
        except ConnectorError as exc:
            return failure(exc)

    @router.get("/capabilities")
    def capabilities(request: Request):
        try:
            current = session(request, "capabilities.read")
            return connector.capabilities(current)
        except ConnectorError as exc:
            return failure(exc)

    @router.post("/jobs")
    def submit_job(request: Request, body: dict[str, Any] = Body(...)):
        try:
            current = session(request, "jobs.submit")
            return {"job": connector.submit(current, body)}
        except ConnectorError as exc:
            return failure(exc)

    @router.get("/jobs")
    def list_jobs(request: Request, limit: int = Query(default=50, ge=1, le=200)):
        try:
            current = session(request, "jobs.read")
            return {"jobs": connector.list_jobs(current, limit)}
        except ConnectorError as exc:
            return failure(exc)

    @router.get("/jobs/{job_id}")
    def get_job(request: Request, job_id: str):
        try:
            current = session(request, "jobs.read")
            return {"job": connector.get_job(current, job_id)}
        except ConnectorError as exc:
            return failure(exc)

    @router.post("/jobs/{job_id}/cancel")
    def cancel_job(request: Request, job_id: str):
        try:
            current = session(request, "jobs.cancel")
            return {"job": connector.cancel(current, job_id)}
        except ConnectorError as exc:
            return failure(exc)

    @router.get("/artifacts/{artifact_id}")
    def get_artifact(request: Request, artifact_id: str):
        try:
            current = session(request, "artifacts.read")
            descriptor, path = connector.artifact(current, artifact_id)
            return FileResponse(
                path,
                media_type=str(descriptor["mime"]),
                headers={
                    "ETag": "\"" + str(descriptor["hash"]) + "\"",
                    "X-Artifact-ID": str(descriptor["id"]),
                    "X-Artifact-SHA256": str(descriptor["hash"]),
                },
            )
        except ConnectorError as exc:
            return failure(exc)

    return router
