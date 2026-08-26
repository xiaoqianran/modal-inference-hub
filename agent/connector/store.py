from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import struct
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

from .contracts import ConnectorError, SAFE_ID, TERMINAL


_DB_VERSION = 1


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class ConnectorStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "connector.sqlite3"
        self.cache_root = self.root / "artifacts" / "sha256"
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.db_path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            version = int(db.execute("PRAGMA user_version").fetchone()[0])
            if version > _DB_VERSION:
                raise RuntimeError(
                    f"Connector DB 版本过新：{version} > {_DB_VERSION}"
                )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS connector_jobs (
                    id TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    remote_job_id TEXT,
                    status TEXT NOT NULL,
                    stage TEXT,
                    attempt INTEGER NOT NULL,
                    event_sequence INTEGER NOT NULL,
                    contract_version TEXT NOT NULL,
                    capability_hash TEXT NOT NULL,
                    capability_revision TEXT NOT NULL,
                    effective_options_json TEXT NOT NULL,
                    model_json TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    recoverable INTEGER NOT NULL DEFAULT 0,
                    result_json TEXT,
                    created_at TEXT NOT NULL,
                    submitted_at TEXT,
                    started_at TEXT,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    UNIQUE(owner, idempotency_key)
                )
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS connector_artifacts (
                    id TEXT PRIMARY KEY,
                    role TEXT NOT NULL,
                    mime TEXT NOT NULL,
                    bytes INTEGER NOT NULL,
                    hash TEXT NOT NULL,
                    local_path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(role, hash)
                )
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS connector_artifact_links (
                    artifact_id TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    producer_job_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(artifact_id, owner, producer_job_id),
                    FOREIGN KEY(artifact_id) REFERENCES connector_artifacts(id),
                    FOREIGN KEY(producer_job_id) REFERENCES connector_jobs(id)
                )
                """
            )
            required = {
                "connector_jobs": {
                    "id", "owner", "provider", "operation", "request_hash",
                    "idempotency_key", "request_json", "remote_job_id", "status",
                    "event_sequence", "contract_version", "capability_hash",
                    "capability_revision", "effective_options_json", "created_at",
                    "updated_at",
                },
                "connector_artifacts": {
                    "id", "role", "mime", "bytes", "hash", "local_path", "created_at",
                },
                "connector_artifact_links": {
                    "artifact_id", "owner", "producer_job_id", "created_at",
                },
            }
            for table, expected in required.items():
                columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
                missing = expected - columns
                if missing:
                    raise RuntimeError(
                        f"Connector DB schema 不兼容：{table} 缺少 {sorted(missing)}"
                    )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_connector_jobs_owner_updated "
                "ON connector_jobs(owner, updated_at DESC)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_connector_artifact_links_owner "
                "ON connector_artifact_links(owner, artifact_id)"
            )
            db.execute(f"PRAGMA user_version = {_DB_VERSION}")


    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, object] | None:
        return None if row is None else dict(row)

    def reserve_job(self, owner: str, request: dict[str, object]) -> tuple[dict[str, object], bool]:
        now = _now()
        request_json = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self._lock, self._connect() as db:
            existing = db.execute(
                "SELECT * FROM connector_jobs WHERE owner = ? AND idempotency_key = ?",
                (owner, request["idempotencyKey"]),
            ).fetchone()
            if existing is not None:
                if existing["request_hash"] != request["requestHash"]:
                    raise ConnectorError("JOB_IDEMPOTENCY_CONFLICT", 409, "idempotency key already owns another request")
                existing_request = json.loads(str(existing["request_json"]))
                provenance = (
                    existing["contract_version"],
                    existing["capability_hash"],
                    existing["capability_revision"],
                    existing_request.get("operationVersion"),
                )
                requested_provenance = (
                    request["contractVersion"],
                    request["capabilityHash"],
                    request["capabilityRevision"],
                    request["operationVersion"],
                )
                if provenance != requested_provenance:
                    raise ConnectorError(
                        "JOB_IDEMPOTENCY_PROVENANCE_CONFLICT",
                        409,
                        "idempotent request belongs to another capability provenance",
                    )
                return dict(existing), True

            job_id = f"job_{uuid.uuid4().hex}"
            db.execute(
                """
                INSERT INTO connector_jobs (
                    id, owner, provider, operation, request_hash, idempotency_key,
                    request_json, status, attempt, event_sequence, contract_version,
                    capability_hash, capability_revision, effective_options_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    owner,
                    request["provider"],
                    request["operation"],
                    request["requestHash"],
                    request["idempotencyKey"],
                    request_json,
                    "accepted",
                    request["contractVersion"],
                    request["capabilityHash"],
                    request["capabilityRevision"],
                    "{}",
                    now,
                    now,
                ),
            )
            row = db.execute("SELECT * FROM connector_jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row), False

    def get_job(self, owner: str, job_id: str) -> dict[str, object]:
        if not SAFE_ID.fullmatch(job_id):
            raise ConnectorError("JOB_NOT_FOUND", 404, "job not found")
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM connector_jobs WHERE owner = ? AND id = ?", (owner, job_id)
            ).fetchone()
        if row is None:
            raise ConnectorError("JOB_NOT_FOUND", 404, "job not found")
        return dict(row)

    def list_jobs(self, owner: str, limit: int = 50) -> list[dict[str, object]]:
        limit = max(1, min(int(limit), 200))
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM connector_jobs WHERE owner = ? ORDER BY updated_at DESC LIMIT ?",
                (owner, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_job(self, job_id: str, **changes: object) -> dict[str, object]:
        allowed = {
            "remote_job_id", "status", "stage", "effective_options_json", "model_json",
            "error_code", "error_message", "recoverable", "result_json", "submitted_at",
            "started_at", "completed_at",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"unknown connector job fields: {sorted(unknown)}")
        with self._lock, self._connect() as db:
            row = db.execute("SELECT * FROM connector_jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            current = dict(row)
            if current["status"] in TERMINAL:
                return current
            material = {key: value for key, value in changes.items() if current.get(key) != value}
            if not material:
                return current
            if "status" in material and material["status"] in TERMINAL and "completed_at" not in material:
                material["completed_at"] = _now()
            material["updated_at"] = _now()
            material["event_sequence"] = int(current["event_sequence"]) + 1
            assignments = ", ".join(f"{key} = ?" for key in material)
            db.execute(
                f"UPDATE connector_jobs SET {assignments} WHERE id = ?",
                (*material.values(), job_id),
            )
            updated = db.execute("SELECT * FROM connector_jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(updated)

    def job_request(self, row: dict[str, object]) -> dict[str, object]:
        return json.loads(str(row["request_json"]))

    def projection(self, row: dict[str, object]) -> dict[str, object]:
        request = self.job_request(row)
        parent = request.get("parent")
        relations = []
        if isinstance(parent, dict) and isinstance(parent.get("jobId"), str):
            relations.append({"type": "parent", "jobId": parent["jobId"]})
        error = None
        if row.get("error_code"):
            error = {
                "code": row["error_code"],
                "message": row.get("error_message"),
                "recoverable": bool(row.get("recoverable")),
            }
        return {
            "id": row["id"],
            "provider": row["provider"],
            "operation": row["operation"],
            "kind": "generation",
            "requestHash": row["request_hash"],
            "idempotencyKey": row["idempotency_key"],
            "contractVersion": row["contract_version"],
            "capabilityHash": row["capability_hash"],
            "capabilityRevision": row["capability_revision"],
            "status": row["status"],
            "stage": row.get("stage"),
            "attempt": row["attempt"],
            "relations": relations,
            "effectiveOptions": json.loads(str(row["effective_options_json"] or "{}")),
            "model": None if row.get("model_json") is None else json.loads(str(row["model_json"])),
            "workflow": None,
            "createdAt": row["created_at"],
            "submittedAt": row.get("submitted_at"),
            "startedAt": row.get("started_at"),
            "updatedAt": row["updated_at"],
            "completedAt": row.get("completed_at"),
            "eventSequence": row["event_sequence"],
            "error": error,
            "result": None if row.get("result_json") is None else json.loads(str(row["result_json"])),
        }

    @staticmethod
    def _validate_content(mime: str, data: bytes) -> None:
        if mime == "image/png":
            if len(data) < 8 or data[:8] != b"\x89PNG\r\n\x1a\n":
                raise ConnectorError("ARTIFACT_INVALID", 502, "invalid PNG artifact")
            return
        if mime == "model/gltf-binary":
            if len(data) < 12:
                raise ConnectorError("ARTIFACT_INVALID", 502, "truncated GLB artifact")
            magic, version, declared = struct.unpack("<4sII", data[:12])
            if magic != b"glTF" or version != 2 or declared != len(data):
                raise ConnectorError("ARTIFACT_INVALID", 502, "invalid GLB artifact")
            return
        raise ConnectorError("ARTIFACT_INVALID", 502, f"unsupported artifact MIME: {mime}")

    def import_bytes(
        self,
        *,
        owner: str,
        producer_job_id: str,
        role: str,
        mime: str,
        data: bytes,
        expected_hash: str | None = None,
        expected_bytes: int | None = None,
    ) -> dict[str, object]:
        self._validate_content(mime, data)
        digest = hashlib.sha256(data).hexdigest()
        canonical_hash = f"sha256:{digest}"
        if expected_hash is not None and expected_hash != canonical_hash:
            raise ConnectorError("ARTIFACT_HASH_MISMATCH", 502, "artifact hash mismatch")
        if expected_bytes is not None and expected_bytes != len(data):
            raise ConnectorError("ARTIFACT_LENGTH_MISMATCH", 502, "artifact length mismatch")
        artifact_id = "art_" + uuid.uuid5(uuid.NAMESPACE_URL, "connector:" + role + ":" + digest).hex
        final = self.cache_root / digest[:2] / digest
        final.parent.mkdir(parents=True, exist_ok=True)
        if not final.is_file():
            temporary = final.with_name(f".{final.name}.{uuid.uuid4().hex}.part")
            try:
                with temporary.open("wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, final)
            finally:
                temporary.unlink(missing_ok=True)
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO connector_artifacts (
                    id, role, mime, bytes, hash, local_path, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (artifact_id, role, mime, len(data), canonical_hash, str(final), _now()),
            )
            row = db.execute("SELECT * FROM connector_artifacts WHERE id = ?", (artifact_id,)).fetchone()
            if row is None:
                raise ConnectorError("ARTIFACT_IDENTITY_CONFLICT", 500, "artifact identity conflict")
            record = dict(row)
            expected = {"role": role, "mime": mime, "bytes": len(data), "hash": canonical_hash}
            if any(record[key] != value for key, value in expected.items()):
                raise ConnectorError("ARTIFACT_IDENTITY_CONFLICT", 500, "artifact identity conflict")
            db.execute(
                """
                INSERT OR IGNORE INTO connector_artifact_links (
                    artifact_id, owner, producer_job_id, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (artifact_id, owner, producer_job_id, _now()),
            )
        return self.artifact_summary(record)


    def import_file(self, **kwargs: object) -> dict[str, object]:
        path = Path(str(kwargs.pop("path")))
        return self.import_bytes(data=path.read_bytes(), **kwargs)

    @staticmethod
    def artifact_summary(row: dict[str, object]) -> dict[str, object]:
        return {
            "id": row["id"],
            "role": row["role"],
            "mime": row["mime"],
            "bytes": row["bytes"],
            "hash": row["hash"],
        }

    def artifact_belongs_to_job(self, owner: str, artifact_id: str, job_id: str) -> bool:
        if not SAFE_ID.fullmatch(artifact_id) or not SAFE_ID.fullmatch(job_id):
            return False
        with self._connect() as db:
            row = db.execute(
                """
                SELECT 1
                FROM connector_artifact_links
                WHERE artifact_id = ? AND owner = ? AND producer_job_id = ?
                LIMIT 1
                """,
                (artifact_id, owner, job_id),
            ).fetchone()
        return row is not None

    def artifact(self, owner: str, artifact_id: str) -> tuple[dict[str, object], Path]:
        if not SAFE_ID.fullmatch(artifact_id):
            raise ConnectorError("ARTIFACT_NOT_FOUND", 404, "artifact not found")
        with self._connect() as db:
            row = db.execute(
                """
                SELECT artifact.*
                FROM connector_artifacts AS artifact
                WHERE artifact.id = ? AND EXISTS (
                    SELECT 1 FROM connector_artifact_links AS link
                    WHERE link.artifact_id = artifact.id AND link.owner = ?
                )
                """,
                (artifact_id, owner),
            ).fetchone()
        if row is None:
            raise ConnectorError("ARTIFACT_NOT_FOUND", 404, "artifact not found")
        record = dict(row)
        path = Path(str(record["local_path"]))
        if not path.is_file():
            raise ConnectorError("ARTIFACT_MISSING", 410, "artifact cache entry is missing")
        data = path.read_bytes()
        self._validate_content(str(record["mime"]), data)
        if len(data) != record["bytes"] or f"sha256:{hashlib.sha256(data).hexdigest()}" != record["hash"]:
            raise ConnectorError("ARTIFACT_INVALID", 500, "artifact cache integrity check failed")
        return self.artifact_summary(record), path
