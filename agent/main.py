from __future__ import annotations

from fastapi import FastAPI, HTTPException
from modal.exception import AuthError, ConnectionError as ModalConnectionError, PermissionDeniedError, TimeoutError as ModalTimeoutError
from pydantic import BaseModel, SecretStr

from agent.hardware import detect_hardware
from agent.modal_client import test_credentials

app = FastAPI(title="modal-3D Local Agent", docs_url=None, redoc_url=None)


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
