from __future__ import annotations

import hmac
import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from modal.exception import (
    AuthError,
    ConnectionError as ModalConnectionError,
    PermissionDeniedError,
    TimeoutError as ModalTimeoutError,
)
from pydantic import BaseModel, SecretStr

from agent.hardware import detect_hardware
from agent.modal_client import test_credentials

app = FastAPI(title="modal-3D Local Agent", docs_url=None, redoc_url=None)
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
            return JSONResponse(status_code=401, content={"detail": "Invalid local session"})
    return await call_next(request)


class ModalCredentials(BaseModel):
    token_id: str
    token_secret: SecretStr


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/hardware")
def hardware() -> dict:
    return detect_hardware()


@app.post("/modal/test")
def modal_test(credentials: ModalCredentials) -> dict:
    if not credentials.token_id.strip() or not credentials.token_secret.get_secret_value().strip():
        raise HTTPException(status_code=400, detail="Token ID and Token Secret are required")
    try:
        test_credentials(credentials.token_id, credentials.token_secret.get_secret_value())
    except (AuthError, PermissionDeniedError) as exc:
        raise HTTPException(status_code=401, detail="Modal authentication failed") from exc
    except (ModalConnectionError, ModalTimeoutError) as exc:
        raise HTTPException(status_code=503, detail="Modal is unavailable") from exc
    return {"ok": True}
