from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Iterable

from agent.storage import data_dir

from .auth import SessionRecord, SessionStore, iso, utcnow
from .contracts import CONTRACT_VERSION, ConnectorError, TERMINAL, validate_submit
from .providers import ProviderAdapter, default_adapters
from .store import ConnectorStore

CONNECTOR_ID = "unified-connector"
CONNECTOR_VERSION = "0.1.0"


class ConnectorService:
    def __init__(
        self,
        *,
        store: ConnectorStore | None = None,
        adapters: Iterable[ProviderAdapter] | None = None,
        pairing_secret: str | None = None,
        allowed_origins: tuple[str, ...] | None = None,
        instance: str | None = None,
    ) -> None:
        self.identity = {
            "id": CONNECTOR_ID,
            "instance": instance or os.environ.get("MODAL_CONNECTOR_INSTANCE") or f"instance_{uuid.uuid4().hex}",
            "version": CONNECTOR_VERSION,
        }
        self.store = store or ConnectorStore(data_dir() / "connector")
        adapter_values = tuple(adapters or default_adapters())
        self.adapters = {adapter.id: adapter for adapter in adapter_values}
        if len(self.adapters) != len(adapter_values):
            raise ValueError("duplicate connector provider id")
        origins = allowed_origins or self._allowed_origins_from_env()
        secret = os.environ.get("MODAL_CONNECTOR_PAIRING_TOKEN", "") if pairing_secret is None else pairing_secret
        self.sessions = SessionStore(
            connector_identity=self.identity,
            pairing_secret=secret,
            allowed_origins=origins,
            snapshot_factory=self._snapshot,
        )

    @staticmethod
    def _allowed_origins_from_env() -> tuple[str, ...]:
        value = os.environ.get("MODAL_CONNECTOR_ALLOWED_ORIGINS", "")
        if value.strip():
            return tuple(item.strip() for item in value.split(",") if item.strip())
        return ("http://localhost:3000", "http://127.0.0.1:3000")

    def _snapshot(self, expires_at: datetime) -> dict[str, object]:
        providers = [self.adapters[key].descriptor() for key in sorted(self.adapters)]
        identity = {
            "contractVersion": CONTRACT_VERSION,
            "connector": self.identity,
            "providers": providers,
        }
        encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        return {
            **identity,
            "revision": f"cap_{digest[:20]}",
            "hash": f"sha256:{digest}",
            "generatedAt": iso(utcnow()),
            "expiresAt": iso(expires_at),
            "cachePolicy": {"maxAgeSeconds": 300},
        }

    def pair(self, body: object, *, approval: str, origin_header: str) -> dict[str, object]:
        if not isinstance(body, dict):
            raise ConnectorError("PAIRING_INVALID", 422, "pair request must be an object")
        scopes = body.get("scopes")
        if not isinstance(scopes, list):
            raise ConnectorError("PAIRING_INVALID", 422, "scopes must be an array")
        origin = str(body.get("origin") or "")
        if not origin_header or origin_header != origin:
            raise ConnectorError("PAIRING_ORIGIN_DENIED", 403, "Origin header must match requested origin")
        return self.sessions.pair(
            approval=approval,
            client_identity=str(body.get("clientIdentity") or ""),
            contract_version=str(body.get("contractVersion") or ""),
            origin=origin,
            scopes=scopes,
        )

    def authenticate(self, authorization: str, origin: str, scope: str) -> SessionRecord:
        return self.sessions.authenticate(authorization, origin, scope)

    def revoke(self, authorization: str, origin: str) -> None:
        self.sessions.revoke(authorization, origin)

    @staticmethod
    def capabilities(session: SessionRecord) -> dict[str, object]:
        return dict(session.capability_snapshot)

    @staticmethod
    def _resolve_capability(session: SessionRecord, request: dict[str, object]) -> dict[str, object]:
        snapshot = session.capability_snapshot
        if request["contractVersion"] != CONTRACT_VERSION:
            raise ConnectorError("CONTRACT_VERSION_MISMATCH", 409, "connector contract version mismatch")
        if request["capabilityHash"] != snapshot["hash"] or request["capabilityRevision"] != snapshot["revision"]:
            raise ConnectorError("CAPABILITY_STALE", 409, "capability snapshot identity mismatch")
        provider = next(
            (item for item in snapshot["providers"] if isinstance(item, dict) and item.get("id") == request["provider"]),
            None,
        )
        if not isinstance(provider, dict):
            raise ConnectorError("PROVIDER_UNKNOWN", 422, "unknown provider")
        capability = next(
            (
                item
                for item in provider.get("capabilities", [])
                if isinstance(item, dict) and item.get("operation") == request["operation"]
            ),
            None,
        )
        if not isinstance(capability, dict):
            raise ConnectorError("OPERATION_UNKNOWN", 422, "unknown operation")
        if provider.get("status") != "available" or capability.get("status") != "available":
            raise ConnectorError("CAPABILITY_UNAVAILABLE", 409, "capability unavailable", recoverable=True)
        if str(capability.get("version")) != request["operationVersion"]:
            raise ConnectorError("CAPABILITY_VERSION_MISMATCH", 409, "operation version mismatch")
        roles = capability.get("output", {}).get("roles", []) if isinstance(capability.get("output"), dict) else []
        requested_roles = set(request["outputRoles"])
        invalid = [role for role in requested_roles if role not in roles]
        if invalid:
            raise ConnectorError("OUTPUT_ROLE_UNSUPPORTED", 422, "unsupported output role")
        output = capability.get("output") if isinstance(capability.get("output"), dict) else {}
        required_roles = set(output.get("required", [])) if isinstance(output, dict) else set()
        if not required_roles.issubset(requested_roles):
            raise ConnectorError("OUTPUT_ROLE_REQUIRED", 422, "required output role missing")
        return capability

    def submit(self, session: SessionRecord, envelope: object) -> dict[str, object]:
        request = validate_submit(envelope)
        self._resolve_capability(session, request)
        adapter = self.adapters.get(str(request["provider"]))
        if adapter is None or adapter.operation != request["operation"]:
            raise ConnectorError("OPERATION_UNKNOWN", 422, "unknown operation")

        parent = request.get("parent")
        parent_id = None
        if isinstance(parent, dict) and parent.get("jobId") is not None:
            parent_id = str(parent["jobId"])
            self.store.get_job(session.owner, parent_id)

        source = request["inputs"].get("sourceArtifact") if isinstance(request["inputs"], dict) else None
        if request["provider"] == "modal-3d":
            if not isinstance(source, dict) or not isinstance(source.get("id"), str):
                raise ConnectorError("INVALID_REQUEST", 422, "modal-3d sourceArtifact is required")
            if parent_id is None:
                raise ConnectorError("INVALID_REQUEST", 422, "modal-3d parent job is required")
            if not self.store.artifact_belongs_to_job(session.owner, str(source["id"]), parent_id):
                raise ConnectorError("ARTIFACT_LINEAGE_MISMATCH", 422, "sourceArtifact is not produced by parent job")

        row, reused = self.store.reserve_job(session.owner, request)
        if reused:
            return self.store.projection(row)

        try:
            remote = adapter.submit(request, self.store, session.owner)
        except ConnectorError as exc:
            status = "connection_required" if exc.recoverable else "failed"
            row = self.store.update_job(
                str(row["id"]),
                status=status,
                stage="submission",
                error_code=exc.code,
                error_message=str(exc),
                recoverable=int(exc.recoverable),
            )
            return self.store.projection(row)
        except Exception:
            row = self.store.update_job(
                str(row["id"]),
                status="connection_required",
                stage="submission",
                error_code="SUBMISSION_OUTCOME_UNKNOWN",
                error_message="provider submission outcome is unknown",
                recoverable=1,
            )
            return self.store.projection(row)

        now = iso(utcnow())
        row = self.store.update_job(
            str(row["id"]),
            remote_job_id=remote.remote_job_id,
            status="running",
            stage="running",
            effective_options_json=json.dumps(remote.effective_options, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            model_json=None if remote.model is None else json.dumps(remote.model, separators=(",", ":")),
            submitted_at=now,
            started_at=now,
            error_code=None,
            error_message=None,
            recoverable=0,
        )
        return self.store.projection(row)

    def _apply_provider_state(
        self,
        session: SessionRecord,
        row: dict[str, object],
        state: dict[str, object],
    ) -> dict[str, object]:
        status = str(state.get("status") or "")
        if status not in {"running", "connection_required", "cancel_requested", "succeeded", "failed", "cancelled", "expired"}:
            return self.store.update_job(
                str(row["id"]),
                status="failed",
                stage="provider",
                error_code="PROVIDER_INVALID_STATUS",
                error_message="provider returned invalid status",
                recoverable=0,
            )
        if row["status"] == "cancel_requested" and status in {"running", "connection_required"}:
            return self.store.update_job(
                str(row["id"]),
                stage="cancel",
                error_code=None if status == "running" else "CONNECTION_REQUIRED",
                error_message=None if status == "running" else "cancel acknowledgement unavailable",
                recoverable=1,
            )
        if status == "succeeded":
            adapter = self.adapters[str(row["provider"])]
            try:
                artifacts = adapter.collect(
                    str(row["remote_job_id"]), state, self.store, session.owner, str(row["id"])
                )
            except ConnectorError as exc:
                return self.store.update_job(
                    str(row["id"]),
                    status="connection_required" if exc.recoverable else "failed",
                    stage="artifact",
                    error_code=exc.code,
                    error_message=str(exc),
                    recoverable=int(exc.recoverable),
                )
            result = {"manifestId": "manifest_" + str(row["id"]), "artifacts": artifacts}
            return self.store.update_job(
                str(row["id"]),
                status="succeeded",
                stage="artifact",
                result_json=json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                error_code=None,
                error_message=None,
                recoverable=0,
            )
        error_code = state.get("error_code") or state.get("errorCode")
        error_message = state.get("error")
        retryable = bool(state.get("retryable"))
        return self.store.update_job(
            str(row["id"]),
            status=status,
            stage="running" if status == "running" else status,
            error_code=None if status == "running" else str(error_code or f"PROVIDER_{status.upper()}"),
            error_message=None if status == "running" else str(error_message or status),
            recoverable=int(retryable or status in {"connection_required", "cancel_requested"}),
        )

    def get_job(self, session: SessionRecord, job_id: str, *, poll: bool = True) -> dict[str, object]:
        row = self.store.get_job(session.owner, job_id)
        if not poll or row["status"] in TERMINAL or not row.get("remote_job_id"):
            return self.store.projection(row)
        adapter = self.adapters[str(row["provider"])]
        try:
            state = adapter.poll(str(row["remote_job_id"]))
        except ConnectorError as exc:
            row = self.store.update_job(
                str(row["id"]),
                status="connection_required" if exc.recoverable else "failed",
                stage="running",
                error_code=exc.code,
                error_message=str(exc),
                recoverable=int(exc.recoverable),
            )
        else:
            row = self._apply_provider_state(session, row, state)
        return self.store.projection(row)

    def list_jobs(self, session: SessionRecord, limit: int = 50) -> list[dict[str, object]]:
        return [self.store.projection(row) for row in self.store.list_jobs(session.owner, limit)]

    def cancel(self, session: SessionRecord, job_id: str) -> dict[str, object]:
        row = self.store.get_job(session.owner, job_id)
        if row["status"] in TERMINAL:
            return self.store.projection(row)
        row = self.store.update_job(
            str(row["id"]), status="cancel_requested", stage="cancel", recoverable=1
        )
        if not row.get("remote_job_id"):
            return self.store.projection(row)
        adapter = self.adapters[str(row["provider"])]
        try:
            state = adapter.cancel(str(row["remote_job_id"]))
        except ConnectorError as exc:
            row = self.store.update_job(
                str(row["id"]),
                error_code=exc.code,
                error_message=str(exc),
                recoverable=1,
            )
        else:
            row = self._apply_provider_state(session, row, state)
        return self.store.projection(row)

    def artifact(self, session: SessionRecord, artifact_id: str) -> tuple[dict[str, object], Path]:
        return self.store.artifact(session.owner, artifact_id)
