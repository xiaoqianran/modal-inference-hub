from __future__ import annotations

import sqlite3
import struct
from dataclasses import dataclass
from pathlib import Path

import pytest

from agent.connector.contracts import ConnectorError, request_hash
from agent.connector.providers import RemoteSubmission
from agent.connector.service import ConnectorService, connector_allowed_origins
from agent.connector.store import ConnectorStore

ORIGIN = "http://localhost:3000"
SCOPES = [
    "capabilities.read",
    "jobs.submit",
    "jobs.read",
    "jobs.cancel",
    "artifacts.read",
]
PNG = b"\x89PNG\r\n\x1a\nconnector-image"
GLB = struct.pack("<4sII", b"glTF", 2, 12)


@dataclass
class FakeAdapter:
    id: str
    operation: str
    role: str
    mime: str
    payload: bytes
    revision: str = "test-v1"
    submit_count: int = 0
    available: bool = True
    fail_submit: bool = False

    def descriptor(self) -> dict[str, object]:
        status = "available" if self.available else "disabled"
        return {
            "id": self.id,
            "displayName": self.id,
            "version": "1",
            "implementationRevision": self.revision,
            "health": "healthy" if self.available else "unavailable",
            "status": status,
            "contractVersion": "1",
            "artifactTransport": "connector-artifact",
            "capabilities": [
                {
                    "operation": self.operation,
                    "version": "1",
                    "displayName": self.operation,
                    "category": "generation",
                    "status": status,
                    "input": {"types": ["test"]},
                    "output": {"roles": [self.role], "required": [self.role], "optional": []},
                    "profiles": {"recommended": {}},
                    "optionsSchema": {"type": "object"},
                    "execution": {"async": True, "stages": ["running", "artifact"]},
                    "prerequisites": {"authMode": "connector-session", "connection": True},
                    "support": {"cancel": True, "resume": True, "idempotency": True},
                    "artifactTransport": "connector-artifact",
                }
            ],
        }

    def submit(
        self, request, store: ConnectorStore, owner: str, *, connector_job_id: str
    ) -> RemoteSubmission:
        del connector_job_id
        self.submit_count += 1
        if self.fail_submit:
            raise ConnectorError("CONNECTION_REQUIRED", 503, "provider unavailable", recoverable=True)
        if self.id == "modal-3d":
            source = request["inputs"]["sourceArtifact"]
            summary, _ = store.artifact(owner, source["id"])
            assert summary["role"] == "primary-image"
            assert summary["hash"] == source["hash"]
        model = str(request["inputs"].get("model") or "test-model")
        return RemoteSubmission(
            remote_job_id=f"remote_{self.id}_{self.submit_count}",
            effective_options={"model": model},
            model={"id": model, "version": None, "revision": None},
        )

    def recover_submission(self, connector_job_id: str, request: dict[str, object]):
        del connector_job_id, request
        return None

    def poll(self, remote_job_id: str) -> dict[str, object]:
        return {"id": remote_job_id, "status": "succeeded"}

    def cancel(self, remote_job_id: str) -> dict[str, object]:
        return {"id": remote_job_id, "status": "cancelled", "retryable": False}

    def collect(self, remote_job_id, state, store: ConnectorStore, owner: str, job_id: str):
        return [
            store.import_bytes(
                owner=owner,
                producer_job_id=job_id,
                role=self.role,
                mime=self.mime,
                data=self.payload,
            )
        ]


def make_service(tmp_path: Path, *adapters: FakeAdapter) -> ConnectorService:
    return ConnectorService(
        store=ConnectorStore(tmp_path / "connector"),
        adapters=adapters,
        pairing_secret="pair-secret",
        allowed_origins=(ORIGIN,),
        instance="instance_test",
    )


def pair(service: ConnectorService):
    response = service.pair(
        {
            "clientIdentity": "agentscape",
            "contractVersion": "1",
            "origin": ORIGIN,
            "scopes": SCOPES,
        },
        approval="pair-secret",
        origin_header=ORIGIN,
    )
    token = response["token"]
    return response, service.authenticate(f"Bearer {token}", ORIGIN, "jobs.submit")


def envelope(session, provider: str, operation: str, inputs: dict, role: str, *, idem: str | None = None):
    body = {
        "provider": provider,
        "operation": operation,
        "operationVersion": "1",
        "contractVersion": "1",
        "idempotencyKey": idem or "idem_test",
        "inputs": inputs,
        "profile": "recommended",
        "options": {},
        "outputRoles": [role],
        "parent": None,
        "retention": None,
        "metadata": None,
        "capabilityHash": session.capability_snapshot["hash"],
        "capabilityRevision": session.capability_snapshot["revision"],
    }
    body["requestHash"] = request_hash(body)
    return body


def test_pairing_freezes_capabilities_and_enforces_origin_scope(tmp_path: Path) -> None:
    adapter = FakeAdapter("modal-2d", "modal-2d.image.text_to_image.v1", "primary-image", "image/png", PNG)
    service = make_service(tmp_path, adapter)
    response, session = pair(service)

    assert response["session"]["capabilityHash"] == session.capability_snapshot["hash"]
    assert response["session"]["allowedOrigins"] == [ORIGIN]
    assert "pair-secret" not in repr(response)

    adapter.available = False
    assert service.capabilities(session)["providers"][0]["status"] == "available"

    token = response["token"]
    with pytest.raises(ConnectorError, match="origin mismatch"):
        service.authenticate(f"Bearer {token}", "http://127.0.0.1:3000", "jobs.read")
    service.revoke(f"Bearer {token}", ORIGIN)
    with pytest.raises(ConnectorError, match="unavailable"):
        service.authenticate(f"Bearer {token}", ORIGIN, "jobs.read")


def test_text_to_image_to_3d_uses_opaque_connector_artifact(tmp_path: Path) -> None:
    image = FakeAdapter("modal-2d", "modal-2d.image.text_to_image.v1", "primary-image", "image/png", PNG)
    model = FakeAdapter("modal-3d", "modal-3d.asset.image_to_3d.v1", "primary-glb", "model/gltf-binary", GLB)
    service = make_service(tmp_path, image, model)
    _, session = pair(service)

    image_request = envelope(
        session,
        image.id,
        image.operation,
        {"prompt": "mossy shrine", "model": "sana-sprint-1.6b", "seed": 42, "guidance": 4.5},
        "primary-image",
        idem="idem_image",
    )
    image_job = service.submit(session, image_request)
    assert image_job["status"] == "running"
    image_done = service.get_job(session, image_job["id"])
    assert image_done["status"] == "succeeded"
    assert image_done["eventSequence"] == 3
    source = image_done["result"]["artifacts"][0]
    assert source["role"] == "primary-image"

    model_request = envelope(
        session,
        model.id,
        model.operation,
        {
            "sourceArtifact": {
                "id": source["id"],
                "role": source["role"],
                "mime": source["mime"],
                "hash": source["hash"],
            },
            "model": "fastsam3d-plus-plus",
            "seed": 42,
        },
        "primary-glb",
        idem="idem_model",
    )
    model_request["parent"] = {"jobId": image_done["id"]}
    model_request["requestHash"] = request_hash(model_request)
    model_job = service.submit(session, model_request)
    model_done = service.get_job(session, model_job["id"])

    assert model_done["status"] == "succeeded"
    assert model_done["relations"] == [{"type": "parent", "jobId": image_done["id"]}]
    artifact = model_done["result"]["artifacts"][0]
    assert artifact["role"] == "primary-glb"
    descriptor, path = service.artifact(session, artifact["id"])
    assert descriptor == artifact
    assert path.read_bytes() == GLB


def test_idempotency_is_atomic_and_conflict_fails_closed(tmp_path: Path) -> None:
    adapter = FakeAdapter("modal-2d", "modal-2d.image.text_to_image.v1", "primary-image", "image/png", PNG)
    service = make_service(tmp_path, adapter)
    _, session = pair(service)
    request = envelope(session, adapter.id, adapter.operation, {"prompt": "a"}, "primary-image")

    first = service.submit(session, request)
    second = service.submit(session, request)
    assert first["id"] == second["id"]
    assert adapter.submit_count == 1

    conflict = dict(request)
    conflict["inputs"] = {"prompt": "b"}
    conflict["requestHash"] = request_hash(conflict)
    with pytest.raises(ConnectorError) as exc:
        service.submit(session, conflict)
    assert exc.value.code == "JOB_IDEMPOTENCY_CONFLICT"
    assert adapter.submit_count == 1


def test_uncertain_submission_is_not_replayed_implicitly(tmp_path: Path) -> None:
    adapter = FakeAdapter(
        "modal-2d", "modal-2d.image.text_to_image.v1", "primary-image", "image/png", PNG, fail_submit=True
    )
    service = make_service(tmp_path, adapter)
    _, session = pair(service)
    request = envelope(session, adapter.id, adapter.operation, {"prompt": "a"}, "primary-image")

    first = service.submit(session, request)
    second = service.submit(session, request)
    assert first["status"] == "connection_required"
    assert first["id"] == second["id"]
    assert adapter.submit_count == 1


def test_capability_provenance_and_request_hash_are_server_verified(tmp_path: Path) -> None:
    adapter = FakeAdapter("modal-2d", "modal-2d.image.text_to_image.v1", "primary-image", "image/png", PNG)
    service = make_service(tmp_path, adapter)
    _, session = pair(service)
    request = envelope(session, adapter.id, adapter.operation, {"prompt": "a"}, "primary-image")

    stale = dict(request)
    stale["capabilityHash"] = "sha256:" + "0" * 64
    with pytest.raises(ConnectorError) as exc:
        service.submit(session, stale)
    assert exc.value.code == "CAPABILITY_STALE"

    tampered = dict(request)
    tampered["inputs"] = {"prompt": "changed"}
    with pytest.raises(ConnectorError) as exc:
        service.submit(session, tampered)
    assert exc.value.code == "REQUEST_HASH_MISMATCH"


def test_artifact_content_is_deduplicated_but_access_is_owner_scoped(tmp_path: Path) -> None:
    store = ConnectorStore(tmp_path / "connector")
    base = {
        "provider": "modal-2d",
        "operation": "modal-2d.image.text_to_image.v1",
        "requestHash": "sha256:" + "1" * 64,
        "idempotencyKey": "idem_a",
        "contractVersion": "1",
        "capabilityHash": "sha256:" + "2" * 64,
        "capabilityRevision": "cap_a",
    }
    job_a, _ = store.reserve_job("owner-a", base)
    other = dict(base, idempotencyKey="idem_b", requestHash="sha256:" + "3" * 64)
    job_b, _ = store.reserve_job("owner-b", other)
    art_a = store.import_bytes(
        owner="owner-a", producer_job_id=job_a["id"], role="primary-image", mime="image/png", data=PNG
    )
    art_b = store.import_bytes(
        owner="owner-b", producer_job_id=job_b["id"], role="primary-image", mime="image/png", data=PNG
    )
    assert art_a["id"] == art_b["id"]
    assert store.artifact("owner-a", art_a["id"])[0] == art_a
    assert store.artifact("owner-b", art_b["id"])[0] == art_b
    with pytest.raises(ConnectorError) as exc:
        store.artifact("owner-c", art_a["id"])
    assert exc.value.code == "ARTIFACT_NOT_FOUND"


def test_modal3d_rejects_artifact_parent_lineage_mismatch(tmp_path: Path) -> None:
    image = FakeAdapter("modal-2d", "modal-2d.image.text_to_image.v1", "primary-image", "image/png", PNG)
    model = FakeAdapter("modal-3d", "modal-3d.asset.image_to_3d.v1", "primary-glb", "model/gltf-binary", GLB)
    service = make_service(tmp_path, image, model)
    _, session = pair(service)

    first_request = envelope(
        session,
        image.id,
        image.operation,
        {"prompt": "first"},
        "primary-image",
        idem="idem_first",
    )
    first = service.get_job(session, service.submit(session, first_request)["id"])
    source = first["result"]["artifacts"][0]

    image.payload = b"\x89PNG\r\n\x1a\nsecond-image"
    second_request = envelope(
        session,
        image.id,
        image.operation,
        {"prompt": "second"},
        "primary-image",
        idem="idem_second",
    )
    second = service.get_job(session, service.submit(session, second_request)["id"])

    body = envelope(
        session,
        model.id,
        model.operation,
        {"sourceArtifact": source, "model": "fastsam3d-plus-plus"},
        "primary-glb",
        idem="idem_bad_lineage",
    )
    body["parent"] = {"jobId": second["id"]}
    body["requestHash"] = request_hash(body)

    with pytest.raises(ConnectorError) as exc:
        service.submit(session, body)
    assert exc.value.code == "ARTIFACT_LINEAGE_MISMATCH"
    assert model.submit_count == 0



def test_idempotent_reuse_rejects_changed_capability_provenance(tmp_path: Path) -> None:
    adapter = FakeAdapter("modal-2d", "modal-2d.image.text_to_image.v1", "primary-image", "image/png", PNG)
    store = ConnectorStore(tmp_path / "connector")
    service = ConnectorService(
        store=store,
        adapters=(adapter,),
        pairing_secret="pair-secret",
        allowed_origins=(ORIGIN,),
        instance="instance_test",
    )
    _, first_session = pair(service)
    first_request = envelope(
        first_session,
        adapter.id,
        adapter.operation,
        {"prompt": "same logical request"},
        "primary-image",
        idem="idem_provenance",
    )
    first = service.submit(first_session, first_request)
    assert first["status"] == "running"
    assert adapter.submit_count == 1

    adapter.revision = "test-v2"
    _, second_session = pair(service)
    second_request = envelope(
        second_session,
        adapter.id,
        adapter.operation,
        {"prompt": "same logical request"},
        "primary-image",
        idem="idem_provenance",
    )
    assert second_request["requestHash"] == first_request["requestHash"]
    assert second_request["capabilityHash"] != first_request["capabilityHash"]

    with pytest.raises(ConnectorError) as exc:
        service.submit(second_session, second_request)
    assert exc.value.code == "JOB_IDEMPOTENCY_PROVENANCE_CONFLICT"
    assert adapter.submit_count == 1


def test_required_output_role_cannot_be_omitted(tmp_path: Path) -> None:
    adapter = FakeAdapter("modal-2d", "modal-2d.image.text_to_image.v1", "primary-image", "image/png", PNG)
    service = make_service(tmp_path, adapter)
    _, session = pair(service)
    body = envelope(session, adapter.id, adapter.operation, {"prompt": "x"}, "primary-image")
    body["outputRoles"] = []
    body["requestHash"] = request_hash(body)

    with pytest.raises(ConnectorError) as exc:
        service.submit(session, body)
    assert exc.value.code == "OUTPUT_ROLE_REQUIRED"
    assert adapter.submit_count == 0


def test_unknown_submission_outcome_is_persisted_and_not_replayed(tmp_path: Path) -> None:
    class CrashingAdapter(FakeAdapter):
        def submit(self, request, store, owner, *, connector_job_id):
            del connector_job_id
            self.submit_count += 1
            raise RuntimeError("unexpected transport boundary failure")

    adapter = CrashingAdapter(
        "modal-2d",
        "modal-2d.image.text_to_image.v1",
        "primary-image",
        "image/png",
        PNG,
    )
    service = make_service(tmp_path, adapter)
    _, session = pair(service)
    body = envelope(session, adapter.id, adapter.operation, {"prompt": "x"}, "primary-image")

    first = service.submit(session, body)
    second = service.submit(session, body)
    assert first["status"] == "connection_required"
    assert first["error"] == {
        "code": "SUBMISSION_OUTCOME_UNKNOWN",
        "message": "provider submission outcome is unknown",
        "recoverable": True,
    }
    assert second["id"] == first["id"]
    assert second["eventSequence"] == first["eventSequence"]
    assert adapter.submit_count == 1


def test_connector_recovers_durable_provider_job_without_resubmitting(tmp_path: Path) -> None:
    class CrashAfterDurableJob(FakeAdapter):
        durable_job_id: str | None = None

        def submit(self, request, store, owner, *, connector_job_id):
            del store, owner
            self.submit_count += 1
            self.durable_job_id = connector_job_id
            raise RuntimeError("connector binding interrupted")

        def recover_submission(self, connector_job_id, request):
            if connector_job_id != self.durable_job_id:
                return None
            model = str(request["inputs"].get("model") or "test-model")
            return RemoteSubmission(
                remote_job_id=connector_job_id,
                effective_options={"model": model},
                model={"id": model, "version": None, "revision": None},
            )

    adapter = CrashAfterDurableJob(
        "modal-2d",
        "modal-2d.image.text_to_image.v1",
        "primary-image",
        "image/png",
        PNG,
    )
    service = make_service(tmp_path, adapter)
    _, session = pair(service)
    body = envelope(
        session,
        adapter.id,
        adapter.operation,
        {"prompt": "recover me", "model": "test-model"},
        "primary-image",
        idem="idem_durable_recovery",
    )

    interrupted = service.submit(session, body)
    assert interrupted["status"] == "connection_required"
    assert adapter.submit_count == 1
    assert adapter.durable_job_id == interrupted["id"]

    recovered = service.get_job(session, interrupted["id"])
    assert recovered["status"] == "succeeded"
    assert recovered["id"] == interrupted["id"]
    assert recovered["result"]["artifacts"][0]["role"] == "primary-image"
    assert adapter.submit_count == 1


def test_cancel_recovers_durable_provider_job_before_sending_cancel(tmp_path: Path) -> None:
    class CrashAfterDurableJob(FakeAdapter):
        durable_job_id: str | None = None
        cancelled_job_id: str | None = None

        def submit(self, request, store, owner, *, connector_job_id):
            del request, store, owner
            self.submit_count += 1
            self.durable_job_id = connector_job_id
            raise RuntimeError("connector binding interrupted")

        def recover_submission(self, connector_job_id, request):
            del request
            if connector_job_id != self.durable_job_id:
                return None
            return RemoteSubmission(
                remote_job_id=connector_job_id,
                effective_options={"model": "test-model"},
                model={"id": "test-model", "version": None, "revision": None},
            )

        def cancel(self, remote_job_id):
            self.cancelled_job_id = remote_job_id
            return {"id": remote_job_id, "status": "cancelled", "retryable": False}

    adapter = CrashAfterDurableJob(
        "modal-2d",
        "modal-2d.image.text_to_image.v1",
        "primary-image",
        "image/png",
        PNG,
    )
    service = make_service(tmp_path, adapter)
    _, session = pair(service)
    body = envelope(
        session,
        adapter.id,
        adapter.operation,
        {"prompt": "cancel me", "model": "test-model"},
        "primary-image",
        idem="idem_durable_cancel",
    )

    interrupted = service.submit(session, body)
    cancelled = service.cancel(session, interrupted["id"])
    assert cancelled["status"] == "cancelled"
    assert adapter.cancelled_job_id == interrupted["id"]
    assert adapter.submit_count == 1



def test_connector_store_sets_and_validates_schema_version(tmp_path: Path) -> None:
    root = tmp_path / "connector-versioned"
    store = ConnectorStore(root)
    with sqlite3.connect(store.db_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1

    with sqlite3.connect(store.db_path) as db, db:
        db.execute("PRAGMA user_version = 0")
    ConnectorStore(root)
    with sqlite3.connect(store.db_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1


def test_connector_store_rejects_future_or_incompatible_schema(tmp_path: Path) -> None:
    future_root = tmp_path / "future-connector"
    future_root.mkdir()
    future_db = future_root / "connector.sqlite3"
    with sqlite3.connect(future_db) as db, db:
        db.execute("PRAGMA user_version = 99")
    with pytest.raises(RuntimeError, match="Connector DB 版本过新"):
        ConnectorStore(future_root)

    broken_root = tmp_path / "broken-connector"
    broken_root.mkdir()
    broken_db = broken_root / "connector.sqlite3"
    with sqlite3.connect(broken_db) as db, db:
        db.execute("CREATE TABLE connector_jobs (id TEXT PRIMARY KEY)")
    with pytest.raises(RuntimeError, match="Connector DB schema 不兼容"):
        ConnectorStore(broken_root)



def test_connector_allowed_origins_share_normalized_env_configuration(monkeypatch) -> None:
    monkeypatch.setenv(
        "MODAL_CONNECTOR_ALLOWED_ORIGINS",
        "https://example.com:443, https://example.com, http://localhost:3100",
    )
    assert connector_allowed_origins() == (
        "https://example.com",
        "http://localhost:3100",
    )

    monkeypatch.setenv("MODAL_CONNECTOR_ALLOWED_ORIGINS", "tauri://localhost")
    with pytest.raises(ConnectorError) as exc:
        connector_allowed_origins()
    assert exc.value.code == "PAIRING_INVALID"


def test_connector_pairing_secret_falls_back_to_agent_session_token(tmp_path: Path, monkeypatch) -> None:
    adapter = FakeAdapter(
        "modal-2d",
        "modal-2d.image.text_to_image.v1",
        "primary-image",
        "image/png",
        PNG,
    )
    monkeypatch.delenv("MODAL_CONNECTOR_PAIRING_TOKEN", raising=False)
    monkeypatch.setenv("MODAL_3D_AGENT_TOKEN", "agent-session-secret")
    service = ConnectorService(
        store=ConnectorStore(tmp_path / "connector"),
        adapters=(adapter,),
        allowed_origins=(ORIGIN,),
        instance="instance_pair_fallback",
    )

    paired = service.pair(
        {
            "clientIdentity": "agentscape",
            "contractVersion": "1",
            "origin": ORIGIN,
            "scopes": SCOPES,
        },
        approval="agent-session-secret",
        origin_header=ORIGIN,
    )
    assert paired["token"]
    assert "agent-session-secret" not in repr(paired)


def test_explicit_connector_pairing_secret_overrides_agent_session(tmp_path: Path, monkeypatch) -> None:
    adapter = FakeAdapter(
        "modal-2d",
        "modal-2d.image.text_to_image.v1",
        "primary-image",
        "image/png",
        PNG,
    )
    monkeypatch.setenv("MODAL_CONNECTOR_PAIRING_TOKEN", "connector-secret")
    monkeypatch.setenv("MODAL_3D_AGENT_TOKEN", "agent-session-secret")
    service = ConnectorService(
        store=ConnectorStore(tmp_path / "connector"),
        adapters=(adapter,),
        allowed_origins=(ORIGIN,),
        instance="instance_pair_override",
    )

    with pytest.raises(ConnectorError) as exc:
        service.pair(
            {
                "clientIdentity": "agentscape",
                "contractVersion": "1",
                "origin": ORIGIN,
                "scopes": SCOPES,
            },
            approval="agent-session-secret",
            origin_header=ORIGIN,
        )
    assert exc.value.code == "PAIRING_REQUIRED"

    paired = service.pair(
        {
            "clientIdentity": "agentscape",
            "contractVersion": "1",
            "origin": ORIGIN,
            "scopes": SCOPES,
        },
        approval="connector-secret",
        origin_header=ORIGIN,
    )
    assert paired["token"]
