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
from agent.modal_client import connect, connected, disconnect

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


class ModalCredentials(BaseModel):
    token_id: str
    token_secret: SecretStr


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
    if not credentials.token_id.strip() or not credentials.token_secret.get_secret_value().strip():
        raise HTTPException(status_code=400, detail="令牌 ID 和令牌密钥不能为空")
    try:
        connect(credentials.token_id, credentials.token_secret.get_secret_value())
    except (AuthError, PermissionDeniedError) as exc:
        raise HTTPException(status_code=401, detail="Modal 身份验证失败") from exc
    except (ModalConnectionError, ModalTimeoutError) as exc:
        raise HTTPException(status_code=503, detail="Modal 服务当前不可用") from exc
    return {"ok": True}


@app.delete("/modal/connect")
def modal_disconnect() -> dict:
    disconnect()
    return {"ok": True}
