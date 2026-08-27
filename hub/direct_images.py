"""Vertical slice: durable caller image input → modal-3D job → GLB artifact."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .sidecars import SidecarClient, SidecarError, project_job

MAX_INPUT_BYTES = 25 * 1024 * 1024
TERMINAL = frozenset({"succeeded", "failed", "cancelled", "expired"})


class DirectImageError(ValueError):
    pass


class DirectImageNotFound(DirectImageError):
    pass


class DirectImageConflict(DirectImageError):
    pass


def now() -> str:
    return datetime.now(UTC).isoformat()


def describe_input(data: bytes, filename: str) -> dict[str, Any]:
    if not data:
        raise DirectImageError("image must not be empty")
    if len(data) > MAX_INPUT_BYTES:
        raise DirectImageError("image exceeds 25 MiB")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        media_type = "image/png"
    elif data.startswith(b"\xff\xd8\xff"):
        media_type = "image/jpeg"
    elif len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        media_type = "image/webp"
    else:
        raise DirectImageError("only PNG, JPEG and WebP images are supported")
    safe_name = Path(filename or "image").name[:200]
    return {
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "mediaType": media_type,
        "name": safe_name,
    }


def new_run(
    *,
    run_id: str,
    source: dict[str, Any],
    model: str,
    profile: str,
    seed: int,
    timestamp: str,
) -> dict[str, Any]:
    if not model.strip() or not profile.strip():
        raise DirectImageError("3D model and profile must not be empty")
    return {
        "id": run_id,
        "source": copy.deepcopy(source),
        "model": model.strip(),
        "profile": profile.strip(),
        "seed": seed,
        "job": {
            "provider": "modal-3d",
            "id": f"hub3d_{run_id}",
            "state": "planned",
            "failure": None,
            "retryable": True,
        },
        "artifact": None,
        "conditioning": None,
        "createdAt": timestamp,
        "updatedAt": timestamp,
    }


def record_run(
    document: dict[str, Any], provider_job: dict[str, Any], timestamp: str
) -> dict[str, Any]:
    projection = project_job(provider_job)
    updated = copy.deepcopy(document)
    updated["job"].update(
        {key: value for key, value in projection.items() if key not in {"artifact", "conditioning"}}
    )
    if "artifact" in projection:
        updated["artifact"] = projection["artifact"]
    if "conditioning" in projection:
        updated["conditioning"] = projection["conditioning"]
    updated["updatedAt"] = timestamp
    return updated


class InputStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, data: bytes, filename: str) -> dict[str, Any]:
        descriptor = describe_input(data, filename)
        destination = self.root / descriptor["sha256"]
        if not destination.exists():
            temporary = destination.with_suffix(f".{uuid.uuid4().hex}.tmp")
            temporary.write_bytes(data)
            try:
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
        return descriptor

    def read(self, descriptor: dict[str, Any]) -> bytes:
        digest = str(descriptor.get("sha256") or "")
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise DirectImageError("stored input descriptor is invalid")
        data = (self.root / digest).read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise DirectImageError("stored input integrity check failed")
        return data


class DirectImageStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS direct_image_runs (
                    id TEXT PRIMARY KEY,
                    document_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, document: dict[str, Any]) -> dict[str, Any]:
        payload = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
        with self._lock, self._connect() as db:
            try:
                db.execute(
                    "INSERT INTO direct_image_runs VALUES (?, ?, ?, ?)",
                    (document["id"], payload, document["createdAt"], document["updatedAt"]),
                )
            except sqlite3.IntegrityError:
                return self.get(document["id"])
        return document

    def get(self, run_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute(
                "SELECT document_json FROM direct_image_runs WHERE id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise DirectImageNotFound("direct image run not found")
        return json.loads(row[0])

    def save(self, document: dict[str, Any]) -> dict[str, Any]:
        with self._lock, self._connect() as db:
            changed = db.execute(
                "UPDATE direct_image_runs SET document_json = ?, updated_at = ? WHERE id = ?",
                (
                    json.dumps(document, ensure_ascii=False, separators=(",", ":")),
                    document["updatedAt"],
                    document["id"],
                ),
            ).rowcount
        if changed != 1:
            raise DirectImageNotFound("direct image run not found")
        return document

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT document_json FROM direct_image_runs ORDER BY updated_at DESC LIMIT ?",
                (max(1, min(limit, 200)),),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]


@dataclass(slots=True)
class DirectImageService:
    store: DirectImageStore
    inputs: InputStore
    asset3d: SidecarClient

    def ingest(self, data: bytes, filename: str) -> dict[str, Any]:
        return self.inputs.put(data, filename)

    def validate_source(self, source: dict[str, Any]) -> dict[str, Any]:
        data = self.inputs.read(source)
        actual = describe_input(data, str(source.get("name") or "image"))
        if actual != source:
            raise DirectImageError("stored input descriptor does not match its content")
        return actual

    def create(
        self,
        source: dict[str, Any],
        *,
        model: str,
        profile: str = "recommended",
        seed: int = 42,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        source = self.validate_source(source)
        identifier = run_id or f"img3d_{uuid.uuid4().hex}"
        planned = new_run(
            run_id=identifier,
            source=source,
            model=model,
            profile=profile,
            seed=seed,
            timestamp=now(),
        )
        current = self.store.create(planned)
        same_intent = all(
            current[key] == planned[key] for key in ("source", "model", "profile", "seed")
        )
        if not same_intent:
            raise DirectImageConflict("direct image run id already belongs to another intent")
        if current["job"]["state"] in {"planned", "uncertain", "connection_required"}:
            return self._dispatch(current)
        return current

    def _dispatch(self, document: dict[str, Any]) -> dict[str, Any]:
        try:
            remote = self.asset3d.submit_asset3d(
                self.inputs.read(document["source"]),
                model=document["model"],
                profile=document["profile"],
                seed=document["seed"],
                job_id=document["job"]["id"],
            )
        except (SidecarError, OSError, DirectImageError) as exc:
            remote = {"status": "uncertain", "error_code": str(exc), "retryable": True}
        updated = record_run(document, remote, now())
        return self.store.save(updated)

    def get(self, run_id: str, *, reconcile: bool = True) -> dict[str, Any]:
        document = self.store.get(run_id)
        if not reconcile or document["job"]["state"] in TERMINAL:
            return document
        try:
            remote = self.asset3d.job(document["job"]["id"])
        except SidecarError:
            return document
        return self.store.save(record_run(document, remote, now()))

    def resume(self, run_id: str) -> dict[str, Any]:
        document = self.store.get(run_id)
        if document["job"]["state"] not in {"planned", "uncertain", "connection_required"}:
            raise DirectImageConflict("direct image run has no uncertain execution to resume")
        return self._dispatch(document)

    def artifact(self, run_id: str) -> tuple[Iterator[bytes], dict[str, Any]]:
        document = self.get(run_id)
        if document["job"]["state"] != "succeeded" or not isinstance(
            document.get("artifact"), dict
        ):
            raise DirectImageConflict("direct image artifact is not ready")
        return self.asset3d.stream_artifact(document["job"]["id"]), document["artifact"]
