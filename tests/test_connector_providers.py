from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from agent.connector.contracts import ConnectorError
from agent.connector.providers import Modal2DAdapter, Modal3DAdapter

PNG = b"\x89PNG\r\n\x1a\nprovider-test"


class FakeStore:
    def __init__(self, source: Path | None = None) -> None:
        self.source = source
        self.imported: dict[str, object] | None = None

    def artifact(self, owner: str, artifact_id: str):
        assert owner == "owner"
        assert artifact_id == "art_source"
        assert self.source is not None
        return (
            {
                "id": artifact_id,
                "role": "primary-image",
                "mime": "image/png",
                "bytes": self.source.stat().st_size,
                "hash": "sha256:" + hashlib.sha256(self.source.read_bytes()).hexdigest(),
            },
            self.source,
        )

    def import_bytes(self, **kwargs):
        self.imported = kwargs
        return {
            "id": "art_cached",
            "role": kwargs["role"],
            "mime": kwargs["mime"],
            "bytes": len(kwargs["data"]),
            "hash": "sha256:" + hashlib.sha256(kwargs["data"]).hexdigest(),
        }


class FakeResponse:
    def __init__(self, data: bytes, headers: dict[str, str]) -> None:
        self.data = data
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, limit: int):
        return self.data[:limit]


def test_modal2d_rejects_unsafe_provider_job_identity(monkeypatch) -> None:
    adapter = Modal2DAdapter("http://127.0.0.1:3212")
    monkeypatch.setattr(
        adapter,
        "_json",
        lambda method, path, body=None: {"id": "../escape", "model": "sana-sprint-1.6b"},
    )
    with pytest.raises(ConnectorError) as exc:
        adapter.submit(
            {"inputs": {"prompt": "x", "model": "sana-sprint-1.6b", "seed": 42}},
            FakeStore(),
            "owner",
            connector_job_id="job_connector",
        )
    assert exc.value.code == "PROVIDER_INVALID_RESPONSE"
    with pytest.raises(ConnectorError) as exc:
        adapter.poll("../escape")
    assert exc.value.code == "PROVIDER_INVALID_RESPONSE"


def artifact_state(digest: str) -> dict[str, object]:
    return {
        "result": {
            "artifact": {
                "id": "artifact_png",
                "role": "primary-image",
                "mime": "image/png",
                "format": "png",
                "bytes": len(PNG),
                "sha256": digest,
                "width": 1024,
                "height": 1024,
            }
        }
    }


def test_modal2d_artifact_headers_must_match_descriptor(monkeypatch) -> None:
    adapter = Modal2DAdapter("http://127.0.0.1:3212")
    digest = hashlib.sha256(PNG).hexdigest()
    monkeypatch.setattr(
        adapter,
        "_request",
        lambda method, path, body=None: FakeResponse(
            PNG,
            {"Content-Type": "image/png", "X-Artifact-ID": "wrong-id", "X-Artifact-SHA256": digest},
        ),
    )
    with pytest.raises(ConnectorError) as exc:
        adapter.collect("job_remote", artifact_state(digest), FakeStore(), "owner", "job_connector")
    assert exc.value.code == "ARTIFACT_INVALID"


def test_modal2d_collects_only_verified_png(monkeypatch) -> None:
    adapter = Modal2DAdapter("http://127.0.0.1:3212")
    digest = hashlib.sha256(PNG).hexdigest()
    monkeypatch.setattr(
        adapter,
        "_request",
        lambda method, path, body=None: FakeResponse(
            PNG,
            {
                "Content-Type": "image/png; charset=binary",
                "X-Artifact-ID": "artifact_png",
                "X-Artifact-SHA256": digest,
            },
        ),
    )
    store = FakeStore()
    artifact = adapter.collect("job_remote", artifact_state(digest), store, "owner", "job_connector")[0]
    assert artifact["role"] == "primary-image"
    assert store.imported is not None
    assert store.imported["expected_hash"] == "sha256:" + digest
    assert store.imported["expected_bytes"] == len(PNG)


def test_modal3d_uses_connector_job_id_for_durable_local_job(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.png"
    source.write_bytes(PNG)
    store = FakeStore(source)
    adapter = Modal3DAdapter()
    canonical = b"canonical-png"
    canonical_digest = hashlib.sha256(canonical).hexdigest()
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "agent.connector.providers.rembg_preprocess.process",
        lambda data: {"canonical_bytes": canonical, "canonical_sha256": canonical_digest},
    )
    monkeypatch.setattr(
        "agent.connector.providers.modal3d_artifacts.put",
        lambda data, suffix: {
            "sha256": canonical_digest,
            "bytes": len(canonical),
            "path": "remote/canonical.png",
        },
    )
    monkeypatch.setattr(
        "agent.connector.providers.generation.submit",
        lambda model, path, profile, seed: {"call_id": "fc-provider-test", "model": model},
    )

    def create(model, call_id, *, job_id=None):
        captured.update(model=model, call_id=call_id, job_id=job_id)
        return {"id": job_id, "model": model, "status": "running"}

    monkeypatch.setattr("agent.connector.providers.modal3d_jobs.create", create)
    request = {
        "inputs": {
            "sourceArtifact": {
                "id": "art_source",
                "role": "primary-image",
                "mime": "image/png",
                "hash": "sha256:" + hashlib.sha256(PNG).hexdigest(),
            },
            "model": "fastsam3d-plus-plus",
            "seed": 42,
        },
        "profile": "recommended",
    }
    result = adapter.submit(
        request,
        store,
        "owner",
        connector_job_id="job_connector_3d",
    )
    assert captured == {
        "model": "fastsam3d-plus-plus",
        "call_id": "fc-provider-test",
        "job_id": "job_connector_3d",
    }
    assert result.remote_job_id == "job_connector_3d"


def test_modal3d_recovers_existing_connector_local_job(monkeypatch) -> None:
    adapter = Modal3DAdapter()
    monkeypatch.setattr(
        "agent.connector.providers.modal3d_jobs.get",
        lambda job_id: {
            "id": job_id,
            "model": "fastsam3d-plus-plus",
            "status": "running",
        },
    )
    recovered = adapter.recover_submission(
        "job_connector_3d",
        {
            "inputs": {"model": "fastsam3d-plus-plus", "seed": 42},
            "profile": "recommended",
        },
    )
    assert recovered is not None
    assert recovered.remote_job_id == "job_connector_3d"
    assert recovered.model == {
        "id": "fastsam3d-plus-plus",
        "version": None,
        "revision": None,
    }
