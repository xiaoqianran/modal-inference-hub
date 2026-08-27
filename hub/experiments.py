"""Vertical slice: human text → image candidates → selection → 3D artifact.

Pure functions form the domain core. SQLite, clocks and sidecar calls are the
imperative shell. Keeping the whole experiment document together makes the
human workflow easy to evolve without cross-table coordination.
"""

from __future__ import annotations

import copy
import json
import re
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

TERMINAL = frozenset({"succeeded", "failed", "cancelled", "expired"})
ACTIVE = frozenset({"planned", "submitting", "queued", "running", "connection_required"})
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,160}$")


class ExperimentError(ValueError):
    pass


class ExperimentNotFound(ExperimentError):
    pass


class ExperimentConflict(ExperimentError):
    pass


def now() -> str:
    return datetime.now(UTC).isoformat()


def new_experiment(
    *,
    experiment_id: str,
    prompt: str,
    candidate_count: int,
    image_model: str,
    seed: int,
    timestamp: str,
) -> dict[str, Any]:
    """Pure constructor for a reproducible experiment intent."""
    prompt = prompt.strip()
    if not prompt:
        raise ExperimentError("prompt must not be empty")
    if not 1 <= candidate_count <= 8:
        raise ExperimentError("candidate_count must be between 1 and 8")
    if not image_model.strip():
        raise ExperimentError("image_model must not be empty")
    candidates = [
        {
            "id": f"candidate-{index + 1}",
            "ordinal": index + 1,
            "seed": seed + index,
            "job": {
                "provider": "modal-2d",
                "id": f"hub2d_{experiment_id}_{index + 1}",
                "state": "planned",
            },
            "artifact": None,
            "failure": None,
        }
        for index in range(candidate_count)
    ]
    return {
        "id": experiment_id,
        "title": prompt[:72],
        "prompt": prompt,
        "createdAt": timestamp,
        "updatedAt": timestamp,
        "image": {"model": image_model, "candidates": candidates},
        "selection": None,
        "asset3d": None,
    }


def record_candidate(
    experiment: dict[str, Any], candidate_id: str, provider_job: dict[str, Any], timestamp: str
) -> dict[str, Any]:
    updated = copy.deepcopy(experiment)
    candidate = next(
        (item for item in updated["image"]["candidates"] if item["id"] == candidate_id), None
    )
    if candidate is None:
        raise ExperimentNotFound("candidate not found")
    projection = project_job(provider_job)
    candidate["job"].update({key: value for key, value in projection.items() if key != "artifact"})
    if "artifact" in projection:
        candidate["artifact"] = projection["artifact"]
    candidate["failure"] = projection.get("failure")
    updated["updatedAt"] = timestamp
    return updated


def select_candidate(
    experiment: dict[str, Any], candidate_id: str, timestamp: str
) -> dict[str, Any]:
    candidate = next(
        (item for item in experiment["image"]["candidates"] if item["id"] == candidate_id), None
    )
    if candidate is None:
        raise ExperimentNotFound("candidate not found")
    if candidate["job"]["state"] != "succeeded" or not isinstance(candidate.get("artifact"), dict):
        raise ExperimentConflict("only a succeeded image candidate can be selected")
    updated = copy.deepcopy(experiment)
    updated["selection"] = {
        "candidateId": candidate_id,
        "selectedAt": timestamp,
        "artifact": copy.deepcopy(candidate["artifact"]),
    }
    # A changed human decision invalidates the old derived 3D attempt.
    updated["asset3d"] = None
    updated["updatedAt"] = timestamp
    return updated


def plan_asset3d(
    experiment: dict[str, Any], *, model: str, profile: str, seed: int, timestamp: str
) -> dict[str, Any]:
    if not experiment.get("selection"):
        raise ExperimentConflict("select an image candidate before generating 3D")
    if not model.strip() or not profile.strip():
        raise ExperimentError("3D model and profile must not be empty")
    updated = copy.deepcopy(experiment)
    existing = updated.get("asset3d")
    if existing and existing["job"]["state"] not in {"uncertain", "connection_required"}:
        raise ExperimentConflict("the experiment already has a 3D attempt")
    updated["asset3d"] = {
        "model": model,
        "profile": profile,
        "seed": seed,
        "job": {
            "provider": "modal-3d",
            "id": f"hub3d_{experiment['id']}",
            "state": "submitting",
            "failure": None,
            "retryable": True,
        },
        "artifact": None,
        "conditioning": None,
    }
    updated["updatedAt"] = timestamp
    return updated


def record_asset3d(
    experiment: dict[str, Any], provider_job: dict[str, Any], timestamp: str
) -> dict[str, Any]:
    if not isinstance(experiment.get("asset3d"), dict):
        raise ExperimentConflict("3D attempt has not been planned")
    updated = copy.deepcopy(experiment)
    projection = project_job(provider_job)
    updated["asset3d"]["job"].update(
        {key: value for key, value in projection.items() if key not in {"artifact", "conditioning"}}
    )
    if "artifact" in projection:
        updated["asset3d"]["artifact"] = projection["artifact"]
    if "conditioning" in projection:
        updated["asset3d"]["conditioning"] = projection["conditioning"]
    updated["updatedAt"] = timestamp
    return updated


def mark_uncertain(
    experiment: dict[str, Any], provider: str, message: str, timestamp: str
) -> dict[str, Any]:
    updated = copy.deepcopy(experiment)
    if isinstance(updated.get("asset3d"), dict):
        updated["asset3d"]["job"].update(
            {"state": "uncertain", "failure": f"{provider}: {message}", "retryable": True}
        )
    updated["updatedAt"] = timestamp
    return updated


def phase(experiment: dict[str, Any]) -> str:
    asset = experiment.get("asset3d")
    if isinstance(asset, dict):
        state = asset["job"]["state"]
        return "complete" if state == "succeeded" else f"asset3d-{state}"
    if experiment.get("selection"):
        return "image-selected"
    states = [item["job"]["state"] for item in experiment["image"]["candidates"]]
    if any(state in ACTIVE for state in states):
        return "generating-images"
    if any(state == "succeeded" for state in states):
        return "select-image"
    return "image-generation-failed"


def public(experiment: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(experiment)
    value["phase"] = phase(value)
    return value


class ExperimentStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS experiments (
                    id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
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
            db.execute(
                "INSERT INTO experiments VALUES (?, 1, ?, ?, ?)",
                (document["id"], payload, document["createdAt"], document["updatedAt"]),
            )
        return document

    def get(self, experiment_id: str) -> tuple[dict[str, Any], int]:
        if not SAFE_ID.fullmatch(experiment_id):
            raise ExperimentNotFound("experiment not found")
        with self._connect() as db:
            row = db.execute(
                "SELECT document_json, version FROM experiments WHERE id = ?", (experiment_id,)
            ).fetchone()
        if row is None:
            raise ExperimentNotFound("experiment not found")
        return json.loads(row[0]), int(row[1])

    def save(self, document: dict[str, Any], version: int) -> dict[str, Any]:
        payload = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
        with self._lock, self._connect() as db:
            changed = db.execute(
                "UPDATE experiments SET version = version + 1, document_json = ?, updated_at = ? "
                "WHERE id = ? AND version = ?",
                (payload, document["updatedAt"], document["id"], version),
            ).rowcount
        if changed != 1:
            raise ExperimentConflict("experiment changed concurrently; retry the command")
        return document

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT document_json FROM experiments ORDER BY updated_at DESC LIMIT ?",
                (max(1, min(limit, 200)),),
            ).fetchall()
        return [public(json.loads(row[0])) for row in rows]


@dataclass(slots=True)
class ExperimentService:
    store: ExperimentStore
    image: SidecarClient
    asset3d: SidecarClient

    def create(
        self, intent: dict[str, Any], *, experiment_id: str | None = None
    ) -> dict[str, Any]:
        experiment_id = experiment_id or f"exp_{uuid.uuid4().hex}"
        if not SAFE_ID.fullmatch(experiment_id):
            raise ExperimentError("experiment id is invalid")
        try:
            existing, _ = self.store.get(experiment_id)
        except ExperimentNotFound:
            pass
        else:
            same_intent = (
                existing["prompt"] == str(intent["prompt"]).strip()
                and existing["image"]["model"] == str(intent["image_model"]).strip()
                and len(existing["image"]["candidates"])
                == int(intent.get("candidate_count", 4))
                and existing["image"]["candidates"][0]["seed"]
                == int(intent.get("seed", 42))
            )
            if not same_intent:
                raise ExperimentConflict("experiment id already belongs to another intent")
            return self.get(existing["id"])
        document = new_experiment(
            experiment_id=experiment_id,
            prompt=str(intent["prompt"]),
            candidate_count=int(intent.get("candidate_count", 4)),
            image_model=str(intent["image_model"]),
            seed=int(intent.get("seed", 42)),
            timestamp=now(),
        )
        self.store.create(document)
        return self._submit_recoverable_images(experiment_id)

    def _submit_recoverable_images(self, experiment_id: str) -> dict[str, Any]:
        document, version = self.store.get(experiment_id)
        for candidate in document["image"]["candidates"]:
            if candidate["job"]["state"] not in {"planned", "uncertain"}:
                continue
            try:
                remote = self.image.submit_image(
                    {
                        "prompt": document["prompt"],
                        "model": document["image"]["model"],
                        "seed": candidate["seed"],
                    },
                    job_id=candidate["job"]["id"],
                )
            except SidecarError as exc:
                remote = {
                    "status": "uncertain",
                    "error_code": str(exc),
                    "retryable": True,
                }
            document = record_candidate(document, candidate["id"], remote, now())
        self.store.save(document, version)
        return public(document)

    def get(self, experiment_id: str, *, reconcile: bool = True) -> dict[str, Any]:
        document, version = self.store.get(experiment_id)
        if not reconcile:
            return public(document)
        changed = False
        for candidate in document["image"]["candidates"]:
            if candidate["job"]["state"] not in ACTIVE | {"uncertain"}:
                continue
            try:
                remote = self.image.job(candidate["job"]["id"])
            except SidecarError:
                continue
            document = record_candidate(document, candidate["id"], remote, now())
            changed = True
        asset = document.get("asset3d")
        if isinstance(asset, dict) and asset["job"]["state"] not in TERMINAL:
            try:
                remote = self.asset3d.job(asset["job"]["id"])
            except SidecarError:
                pass
            else:
                document = record_asset3d(document, remote, now())
                changed = True
        if changed:
            self.store.save(document, version)
        return public(document)

    def select(self, experiment_id: str, candidate_id: str) -> dict[str, Any]:
        document, version = self.store.get(experiment_id)
        updated = select_candidate(document, candidate_id, now())
        self.store.save(updated, version)
        return public(updated)

    def generate_asset3d(self, experiment_id: str, intent: dict[str, Any]) -> dict[str, Any]:
        document, version = self.store.get(experiment_id)
        planned = plan_asset3d(
            document,
            model=str(intent["model"]),
            profile=str(intent.get("profile", "recommended")),
            seed=int(intent.get("seed", 42)),
            timestamp=now(),
        )
        self.store.save(planned, version)  # Durable intent before any network effect.
        return self._dispatch_asset3d(planned)

    def _dispatch_asset3d(self, planned: dict[str, Any]) -> dict[str, Any]:
        selected_id = planned["selection"]["candidateId"]
        selected = next(
            item for item in planned["image"]["candidates"] if item["id"] == selected_id
        )
        try:
            source, _ = self.image.artifact(selected["job"]["id"])
            remote = self.asset3d.submit_asset3d(
                source,
                model=planned["asset3d"]["model"],
                profile=planned["asset3d"]["profile"],
                seed=planned["asset3d"]["seed"],
                job_id=planned["asset3d"]["job"]["id"],
            )
            updated = record_asset3d(planned, remote, now())
        except SidecarError as exc:
            updated = mark_uncertain(planned, exc.provider, str(exc), now())
        _, current_version = self.store.get(planned["id"])
        self.store.save(updated, current_version)
        return public(updated)

    def resume(self, experiment_id: str) -> dict[str, Any]:
        """Retry only a durable, explicitly uncertain boundary with the same job ID."""
        document, _ = self.store.get(experiment_id)
        asset = document.get("asset3d")
        if isinstance(asset, dict) and asset["job"]["state"] in {
            "uncertain",
            "connection_required",
        }:
            return self._dispatch_asset3d(document)
        if any(
            item["job"]["state"] in {"planned", "uncertain"}
            for item in document["image"]["candidates"]
        ):
            return self._submit_recoverable_images(experiment_id)
        raise ExperimentConflict("the experiment has no uncertain execution to resume")

    def cancel(self, experiment_id: str) -> dict[str, Any]:
        document, version = self.store.get(experiment_id)
        for candidate in document["image"]["candidates"]:
            if candidate["job"]["state"] in TERMINAL:
                continue
            try:
                remote = self.image.cancel(candidate["job"]["id"])
            except SidecarError:
                continue
            document = record_candidate(document, candidate["id"], remote, now())
        asset = document.get("asset3d")
        if isinstance(asset, dict) and asset["job"]["state"] not in TERMINAL:
            try:
                remote = self.asset3d.cancel(asset["job"]["id"])
            except SidecarError:
                pass
            else:
                document = record_asset3d(document, remote, now())
        self.store.save(document, version)
        return public(document)

    def candidate_artifact(
        self, experiment_id: str, candidate_id: str
    ) -> tuple[bytes, dict[str, str]]:
        document, _ = self.store.get(experiment_id)
        candidate = next(
            (item for item in document["image"]["candidates"] if item["id"] == candidate_id), None
        )
        if candidate is None or candidate["job"]["state"] != "succeeded":
            raise ExperimentNotFound("candidate artifact not found")
        return self.image.artifact(candidate["job"]["id"])

    def output_artifact(self, experiment_id: str) -> tuple[Iterator[bytes], dict[str, Any]]:
        document, _ = self.store.get(experiment_id)
        asset = document.get("asset3d")
        if not isinstance(asset, dict) or asset["job"]["state"] != "succeeded":
            raise ExperimentNotFound("3D artifact not found")
        descriptor = asset.get("artifact")
        if not isinstance(descriptor, dict):
            raise ExperimentNotFound("3D artifact descriptor not found")
        return self.asset3d.stream_artifact(asset["job"]["id"]), descriptor
