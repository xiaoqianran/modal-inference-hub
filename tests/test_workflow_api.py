from __future__ import annotations

import asyncio
from pathlib import Path

from httpx import ASGITransport, AsyncClient, Response

from hub.app import create_app
from hub.experiments import ExperimentService, ExperimentStore
from hub.sidecars import SidecarConfig, SidecarError

PNG = b"\x89PNG\r\n\x1a\nimage"
GLB = b"glTF" + b"\x00" * 20


def request(app, method: str, path: str, **kwargs) -> Response:
    async def send() -> Response:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


class FakeSidecar:
    def __init__(self, provider: str) -> None:
        self.config = SidecarConfig(provider, "http://unused", "X-Test")
        self.states: dict[str, dict] = {}

    def health(self):
        return {"ok": True, "modal_connected": True}

    def models(self):
        return [{"id": f"{self.config.provider}-model", "status": "enabled"}]

    def connect(self, token_id, token_secret):
        assert token_id == "id"
        assert token_secret == "secret"
        return {"connected": True}

    def disconnect(self):
        return {"connected": False}

    def submit_image(self, intent, *, job_id):
        self.states[job_id] = {
            "status": "succeeded",
            "result": {
                "artifact": {
                    "id": f"art-{job_id}",
                    "role": "primary-image",
                    "mediaType": "image/png",
                    "digest": f"sha256:{intent['seed']:064x}",
                }
            },
        }
        return {"id": job_id, "status": "running"}

    def submit_asset3d(self, source, *, model, profile, seed, job_id):
        assert source == PNG
        assert (model, profile, seed) == ("modal-3d-model", "recommended", 42)
        self.states[job_id] = {
            "status": "succeeded",
            "result": {
                "artifact": {
                    "id": "mesh-1",
                    "role": "primary-glb",
                    "mediaType": "model/gltf-binary",
                    "digest": "sha256:" + "f" * 64,
                },
                "conditioning": {"strategy": "birefnet"},
            },
        }
        return {"id": job_id, "status": "running"}

    def job(self, job_id):
        return self.states[job_id]

    def cancel(self, job_id):
        return {"id": job_id, "status": "cancelled", "retryable": False}

    def artifact(self, job_id):
        media = "image/png" if self.config.provider == "modal-2d" else "model/gltf-binary"
        return (PNG if media == "image/png" else GLB), {"etag": '"test"'}

    def stream_artifact(self, job_id):
        yield GLB[:8]
        yield GLB[8:]


class FlakyImage(FakeSidecar):
    def __init__(self) -> None:
        super().__init__("modal-2d")
        self.attempts: list[str] = []

    def submit_image(self, intent, *, job_id):
        self.attempts.append(job_id)
        if len(self.attempts) == 1:
            raise SidecarError("modal-2d", "response lost")
        return super().submit_image(intent, job_id=job_id)


def test_complete_2d_selection_3d_slice(tmp_path: Path) -> None:
    image = FakeSidecar("modal-2d")
    asset3d = FakeSidecar("modal-3d")
    service = ExperimentService(
        ExperimentStore(tmp_path / "experiments.sqlite3"),
        image,
        asset3d,  # type: ignore[arg-type]
    )
    app = create_app(service)

    providers = request(app, "GET", "/api/providers").json()["providers"]
    assert [item["id"] for item in providers] == ["modal-2d", "modal-3d"]
    connected = request(
        app,
        "POST",
        "/api/providers/modal-2d/connection",
        json={"token_id": "id", "token_secret": "secret"},
    )
    assert connected.json() == {"connected": True}

    created = request(
        app,
        "POST",
        "/api/experiments",
        json={
            "prompt": "brass telescope",
            "candidate_count": 2,
            "image_model": "modal-2d-model",
            "seed": 7,
        },
    )
    assert created.status_code == 201
    experiment = request(app, "GET", f"/api/experiments/{created.json()['id']}").json()
    assert experiment["phase"] == "select-image"
    assert all(item["artifact"] for item in experiment["image"]["candidates"])

    selected = request(
        app,
        "POST",
        f"/api/experiments/{experiment['id']}/selection",
        json={"candidate_id": "candidate-2"},
    )
    assert selected.json()["selection"]["candidateId"] == "candidate-2"

    started = request(
        app,
        "POST",
        f"/api/experiments/{experiment['id']}/asset3d",
        json={"model": "modal-3d-model", "profile": "recommended", "seed": 42},
    )
    assert started.json()["asset3d"]["job"]["state"] == "running"
    completed = request(app, "GET", f"/api/experiments/{experiment['id']}").json()
    assert completed["phase"] == "complete"
    assert completed["asset3d"]["conditioning"]["strategy"] == "birefnet"

    artifact = request(app, "GET", f"/api/experiments/{experiment['id']}/artifact")
    assert artifact.status_code == 200
    assert artifact.content == GLB


def test_session_token_is_required(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MODAL_HUB_SESSION_TOKEN", "secret")
    service = ExperimentService(
        ExperimentStore(tmp_path / "db.sqlite3"),
        FakeSidecar("modal-2d"),  # type: ignore[arg-type]
        FakeSidecar("modal-3d"),  # type: ignore[arg-type]
    )
    app = create_app(service)
    assert request(app, "GET", "/health").status_code == 401
    response = request(app, "GET", "/health", headers={"X-Modal-Hub-Session": "secret"})
    assert response.json() == {"ok": True}


def test_uncertain_submission_resumes_with_the_same_sidecar_job_id(tmp_path: Path) -> None:
    image = FlakyImage()
    service = ExperimentService(
        ExperimentStore(tmp_path / "db.sqlite3"),
        image,  # type: ignore[arg-type]
        FakeSidecar("modal-3d"),  # type: ignore[arg-type]
    )
    app = create_app(service)
    created = request(
        app,
        "POST",
        "/api/experiments",
        json={
            "prompt": "recoverable image",
            "candidate_count": 1,
            "image_model": "modal-2d-model",
        },
    ).json()
    assert created["image"]["candidates"][0]["job"]["state"] == "uncertain"
    resumed = request(app, "POST", f"/api/experiments/{created['id']}/resume").json()
    assert resumed["image"]["candidates"][0]["job"]["state"] == "running"
    assert image.attempts == [f"hub2d_{created['id']}_1"] * 2
