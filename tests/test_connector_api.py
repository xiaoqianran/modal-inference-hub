from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI

from agent.connector.api import create_router
from agent.connector.contracts import request_hash
from agent.connector.providers import RemoteSubmission
from agent.connector.service import ConnectorService
from agent.connector.store import ConnectorStore

ORIGIN = "http://localhost:3000"
SCOPES = ["capabilities.read", "jobs.submit", "jobs.read", "jobs.cancel", "artifacts.read"]
PNG = b"\x89PNG\r\n\x1a\nhttp-image"


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self):
        return json.loads(self.body)


async def request(app, method: str, target: str, *, headers=None, payload=None) -> Response:
    parsed = urlsplit(target)
    raw_headers = [(str(k).lower().encode(), str(v).encode()) for k, v in (headers or {}).items()]
    body = b"" if payload is None else json.dumps(payload, separators=(",", ":")).encode()
    if payload is not None:
        raw_headers.append((b"content-type", b"application/json"))
        raw_headers.append((b"content-length", str(len(body)).encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": parsed.path,
        "raw_path": parsed.path.encode(),
        "query_string": parsed.query.encode(),
        "root_path": "",
        "headers": raw_headers,
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 39001),
    }
    sent: list[dict] = []
    delivered = False

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    start = next(item for item in sent if item["type"] == "http.response.start")
    chunks = [item.get("body", b"") for item in sent if item["type"] == "http.response.body"]
    response_headers = {k.decode().lower(): v.decode() for k, v in start.get("headers", [])}
    return Response(start["status"], response_headers, b"".join(chunks))


class Adapter:
    id = "modal-2d"
    operation = "modal-2d.image.text_to_image.v1"

    def __init__(self) -> None:
        self.cancelled = False

    def descriptor(self):
        return {
            "id": self.id,
            "displayName": "Modal 2D",
            "version": "1",
            "implementationRevision": "test-v1",
            "health": "healthy",
            "status": "available",
            "contractVersion": "1",
            "artifactTransport": "connector-artifact",
            "capabilities": [{
                "operation": self.operation,
                "version": "1",
                "displayName": "Text to Image",
                "category": "image-generation",
                "status": "available",
                "input": {"types": ["text"]},
                "output": {"roles": ["primary-image"], "required": ["primary-image"], "optional": []},
                "profiles": {"recommended": {}},
                "optionsSchema": {"type": "object"},
                "execution": {"async": True, "stages": ["running", "artifact"], "durationClass": "medium", "costClass": "gpu"},
                "prerequisites": {"authMode": "connector-session", "connection": True},
                "support": {"cancel": True, "resume": True, "idempotency": True},
                "artifactTransport": "connector-artifact",
            }],
        }

    def submit(self, request, store, owner, *, connector_job_id):
        del connector_job_id
        model = str(request["inputs"].get("model"))
        return RemoteSubmission("remote_image", {"model": model}, {"id": model, "version": None, "revision": None})

    def recover_submission(self, connector_job_id, request):
        del connector_job_id, request
        return None

    def poll(self, remote_job_id):
        return {"id": remote_job_id, "status": "succeeded"}

    def cancel(self, remote_job_id):
        self.cancelled = True
        return {"id": remote_job_id, "status": "cancelled", "retryable": False}

    def collect(self, remote_job_id, state, store, owner, job_id):
        return [store.import_bytes(owner=owner, producer_job_id=job_id, role="primary-image", mime="image/png", data=PNG)]


def make_app(tmp_path: Path):
    service = ConnectorService(
        store=ConnectorStore(tmp_path / "connector"),
        adapters=(Adapter(),),
        pairing_secret="pair-secret",
        allowed_origins=(ORIGIN,),
        instance="instance_http_test",
    )
    app = FastAPI()
    app.include_router(create_router(service))
    return app, service


def auth(token: str):
    return {"Authorization": f"Bearer {token}", "Origin": ORIGIN}


def job_body(snapshot):
    body = {
        "provider": "modal-2d",
        "operation": "modal-2d.image.text_to_image.v1",
        "operationVersion": "1",
        "contractVersion": "1",
        "idempotencyKey": "idem_http_image",
        "inputs": {"prompt": "mossy shrine", "model": "sana-sprint-1.6b"},
        "profile": "recommended",
        "options": {},
        "outputRoles": ["primary-image"],
        "parent": None,
        "retention": None,
        "metadata": None,
        "capabilityHash": snapshot["hash"],
        "capabilityRevision": snapshot["revision"],
    }
    body["requestHash"] = request_hash(body)
    return body


def pair(app):
    response = asyncio.run(request(
        app,
        "POST",
        "/connector/v1/session",
        headers={"Origin": ORIGIN, "X-Connector-Pairing": "pair-secret"},
        payload={"clientIdentity": "agentscape", "contractVersion": "1", "origin": ORIGIN, "scopes": SCOPES},
    ))
    assert response.status == 200
    return response.json()


def test_http_round_trip(tmp_path: Path) -> None:
    app, _ = make_app(tmp_path)
    paired = pair(app)
    token = paired["token"]
    assert token and "pair-secret" not in repr(paired)

    caps = asyncio.run(request(app, "GET", "/connector/v1/capabilities", headers=auth(token)))
    assert caps.status == 200
    snapshot = caps.json()
    assert snapshot["hash"] == paired["session"]["capabilityHash"]

    submitted = asyncio.run(request(
        app, "POST", "/connector/v1/jobs", headers=auth(token), payload=job_body(snapshot)
    ))
    assert submitted.status == 200
    job = submitted.json()["job"]
    assert job["status"] == "running"
    assert job["eventSequence"] == 2

    finished = asyncio.run(request(app, "GET", f"/connector/v1/jobs/{job['id']}", headers=auth(token)))
    assert finished.status == 200
    job = finished.json()["job"]
    assert job["status"] == "succeeded"
    assert job["eventSequence"] == 3
    artifact = job["result"]["artifacts"][0]

    download = asyncio.run(request(
        app,
        "GET",
        f"/connector/v1/artifacts/{artifact['id']}",
        headers={**auth(token), "Accept": "image/png"},
    ))
    assert download.status == 200
    assert download.body == PNG
    assert download.headers["x-artifact-id"] == artifact["id"]
    assert download.headers["x-artifact-sha256"] == artifact["hash"]


def test_http_origin_revoke_and_cancel(tmp_path: Path) -> None:
    app, service = make_app(tmp_path)
    bad = asyncio.run(request(
        app,
        "POST",
        "/connector/v1/session",
        headers={"Origin": ORIGIN, "X-Connector-Pairing": "wrong"},
        payload={"clientIdentity": "agentscape", "contractVersion": "1", "origin": ORIGIN, "scopes": SCOPES},
    ))
    assert bad.status == 401
    assert bad.json() == {"code": "PAIRING_REQUIRED"}

    token = pair(app)["token"]
    denied = asyncio.run(request(
        app,
        "GET",
        "/connector/v1/capabilities",
        headers={"Authorization": f"Bearer {token}", "Origin": "http://127.0.0.1:3000"},
    ))
    assert denied.status == 403

    snapshot = asyncio.run(request(app, "GET", "/connector/v1/capabilities", headers=auth(token))).json()
    job = asyncio.run(request(
        app, "POST", "/connector/v1/jobs", headers=auth(token), payload=job_body(snapshot)
    )).json()["job"]
    cancelled = asyncio.run(request(
        app, "POST", f"/connector/v1/jobs/{job['id']}/cancel", headers=auth(token)
    )).json()["job"]
    assert cancelled["status"] == "cancelled"
    assert service.adapters["modal-2d"].cancelled is True

    revoked = asyncio.run(request(app, "DELETE", "/connector/v1/session", headers=auth(token)))
    assert revoked.status == 200
    after = asyncio.run(request(app, "GET", "/connector/v1/capabilities", headers=auth(token)))
    assert after.status == 401
