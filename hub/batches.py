"""Vertical slice: bounded batches that reference existing workflow runs.

The Batch owns membership, scheduling policy and summary only. Experiment,
DirectImage and Sidecar documents remain their respective sources of truth.
"""

from __future__ import annotations

import copy
import json
import sqlite3
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .direct_images import DirectImageService
from .experiments import ExperimentService

MAX_BATCH_ITEMS = 50


class BatchError(ValueError):
    pass


class BatchNotFound(BatchError):
    pass


class BatchConflict(BatchError):
    pass


def now() -> str:
    return datetime.now(UTC).isoformat()


def normalize_prompts(values: list[str]) -> list[str]:
    prompts = list(dict.fromkeys(value.strip() for value in values if value.strip()))
    if not prompts:
        raise BatchError("batch requires at least one prompt")
    if len(prompts) > MAX_BATCH_ITEMS:
        raise BatchError(f"batch supports at most {MAX_BATCH_ITEMS} prompts")
    if any(len(prompt) > 4000 for prompt in prompts):
        raise BatchError("a prompt exceeds 4000 characters")
    return prompts


def new_batch(
    *,
    batch_id: str,
    kind: str,
    sources: list[dict[str, Any]],
    options: dict[str, Any],
    timestamp: str,
) -> dict[str, Any]:
    if kind not in {"prompts", "images"}:
        raise BatchError("unsupported batch kind")
    if not sources or len(sources) > MAX_BATCH_ITEMS:
        raise BatchError(f"batch item count must be between 1 and {MAX_BATCH_ITEMS}")
    target_kind = "experiment" if kind == "prompts" else "direct-image"
    prefix = "exp" if kind == "prompts" else "img3d"
    items = [
        {
            "id": f"item-{index:03d}",
            "ordinal": index,
            "source": copy.deepcopy(source),
            "state": "planned",
            "target": {"kind": target_kind, "id": f"{prefix}_{batch_id}_{index:03d}"},
            "error": None,
        }
        for index, source in enumerate(sources, start=1)
    ]
    return {
        "id": batch_id,
        "kind": kind,
        "options": copy.deepcopy(options),
        "items": items,
        "createdAt": timestamp,
        "updatedAt": timestamp,
    }


def update_item(
    document: dict[str, Any], item_id: str, state: str, error: str | None, timestamp: str
) -> dict[str, Any]:
    updated = copy.deepcopy(document)
    item = next((value for value in updated["items"] if value["id"] == item_id), None)
    if item is None:
        raise BatchNotFound("batch item not found")
    item["state"] = state
    item["error"] = error[:500] if error else None
    updated["updatedAt"] = timestamp
    return updated


def public(document: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(document)
    counts: dict[str, int] = {}
    for item in value["items"]:
        counts[item["state"]] = counts.get(item["state"], 0) + 1
    states = set(counts)
    if states & {"planned", "submitting", "running", "uncertain"}:
        state = "running"
    elif "awaiting_review" in states:
        state = "awaiting_review"
    elif states == {"succeeded"}:
        state = "succeeded"
    elif states <= {"failed", "cancelled", "expired"}:
        state = "failed"
    else:
        state = "partial"
    value["state"] = state
    value["summary"] = {"total": len(value["items"]), **counts}
    return value


class BatchStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS batches (
                    id TEXT PRIMARY KEY,
                    document_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            rows = db.execute("SELECT id, document_json FROM batches").fetchall()
            for batch_id, payload in rows:
                document = json.loads(payload)
                changed = False
                for item in document["items"]:
                    if item["state"] == "submitting":
                        item["state"] = "uncertain"
                        item["error"] = "Hub 在提交期间退出；可使用原目标 ID 恢复"
                        changed = True
                if changed:
                    document["updatedAt"] = now()
                    db.execute(
                        "UPDATE batches SET document_json = ?, updated_at = ? WHERE id = ?",
                        (
                            json.dumps(document, ensure_ascii=False, separators=(",", ":")),
                            document["updatedAt"],
                            batch_id,
                        ),
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
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO batches VALUES (?, ?, ?, ?)",
                (
                    document["id"],
                    json.dumps(document, ensure_ascii=False, separators=(",", ":")),
                    document["createdAt"],
                    document["updatedAt"],
                ),
            )
        return public(document)

    def get_document(self, batch_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute(
                "SELECT document_json FROM batches WHERE id = ?", (batch_id,)
            ).fetchone()
        if row is None:
            raise BatchNotFound("batch not found")
        return json.loads(row[0])

    def save(self, document: dict[str, Any]) -> dict[str, Any]:
        with self._lock, self._connect() as db:
            changed = db.execute(
                "UPDATE batches SET document_json = ?, updated_at = ? WHERE id = ?",
                (
                    json.dumps(document, ensure_ascii=False, separators=(",", ":")),
                    document["updatedAt"],
                    document["id"],
                ),
            ).rowcount
        if changed != 1:
            raise BatchNotFound("batch not found")
        return public(document)

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT document_json FROM batches ORDER BY updated_at DESC LIMIT ?",
                (max(1, min(limit, 100)),),
            ).fetchall()
        return [public(json.loads(row[0])) for row in rows]


Background = Callable[[Callable[[], None]], None]


def _thread(task: Callable[[], None]) -> None:
    threading.Thread(target=task, daemon=True, name="batch-dispatch").start()


@dataclass(slots=True)
class BatchService:
    store: BatchStore
    experiments: ExperimentService
    direct_images: DirectImageService
    background: Background = _thread
    _lock: threading.RLock = field(init=False, repr=False)
    _active: set[str] = field(init=False, repr=False)
    _provider_slot: threading.Semaphore = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._lock = threading.RLock()
        self._active = set()
        self._provider_slot = threading.Semaphore(1)

    def create_prompts(self, intent: dict[str, Any]) -> dict[str, Any]:
        prompts = normalize_prompts([str(value) for value in intent["prompts"]])
        sources = [{"prompt": prompt} for prompt in prompts]
        options = {
            "candidate_count": int(intent.get("candidate_count", 4)),
            "image_model": str(intent["image_model"]),
            "seed": int(intent.get("seed", 42)),
        }
        return self._create("prompts", sources, options)

    def create_images(
        self, sources: list[dict[str, Any]], *, model: str, profile: str, seed: int
    ) -> dict[str, Any]:
        validated = []
        seen: set[str] = set()
        for source in sources:
            value = self.direct_images.validate_source(source)
            if value["sha256"] not in seen:
                validated.append(value)
                seen.add(value["sha256"])
        return self._create(
            "images", validated, {"model": model, "profile": profile, "seed": seed}
        )

    def _create(
        self, kind: str, sources: list[dict[str, Any]], options: dict[str, Any]
    ) -> dict[str, Any]:
        batch_id = f"batch_{uuid.uuid4().hex}"
        document = new_batch(
            batch_id=batch_id,
            kind=kind,
            sources=sources,
            options=options,
            timestamp=now(),
        )
        created = self.store.create(document)
        self._schedule(batch_id)
        return created

    def _schedule(self, batch_id: str) -> None:
        with self._lock:
            if batch_id in self._active:
                raise BatchConflict("batch is already running")
            self._active.add(batch_id)

        def execute() -> None:
            try:
                self._dispatch(batch_id)
            finally:
                with self._lock:
                    self._active.discard(batch_id)

        self.background(execute)

    def _dispatch(self, batch_id: str) -> None:
        document = self.store.get_document(batch_id)
        for item in document["items"]:
            if item["state"] not in {"planned", "uncertain"}:
                continue
            document = update_item(document, item["id"], "submitting", None, now())
            self.store.save(document)
            try:
                with self._provider_slot:
                    if document["kind"] == "prompts":
                        child = self.experiments.create(
                            {
                                "prompt": item["source"]["prompt"],
                                "candidate_count": document["options"]["candidate_count"],
                                "image_model": document["options"]["image_model"],
                                "seed": document["options"]["seed"],
                            },
                            experiment_id=item["target"]["id"],
                        )
                        state = _experiment_state(child["phase"])
                    else:
                        child = self.direct_images.create(
                            item["source"],
                            model=document["options"]["model"],
                            profile=document["options"]["profile"],
                            seed=document["options"]["seed"],
                            run_id=item["target"]["id"],
                        )
                        state = _job_state(child["job"]["state"])
                document = update_item(document, item["id"], state, None, now())
            except Exception as exc:  # noqa: BLE001 - isolate one batch item from the rest.
                document = update_item(document, item["id"], "failed", str(exc), now())
            self.store.save(document)

    def get(self, batch_id: str, *, reconcile: bool = True) -> dict[str, Any]:
        document = self.store.get_document(batch_id)
        if not reconcile:
            return public(document)
        with self._lock:
            if batch_id in self._active:
                return public(document)
        changed = False
        for item in document["items"]:
            if item["state"] in {"failed", "succeeded", "cancelled", "expired"}:
                continue
            try:
                if item["target"]["kind"] == "experiment":
                    child = self.experiments.get(item["target"]["id"])
                    state = _experiment_state(child["phase"])
                else:
                    child = self.direct_images.get(item["target"]["id"])
                    state = _job_state(child["job"]["state"])
            except Exception:  # noqa: BLE001 - a not-yet-created deterministic child is normal.
                continue
            if state != item["state"]:
                document = update_item(document, item["id"], state, None, now())
                changed = True
        return self.store.save(document) if changed else public(document)

    def resume(self, batch_id: str) -> dict[str, Any]:
        document = self.store.get_document(batch_id)
        recoverable = any(
            item["state"] in {"planned", "uncertain"}
            for item in document["items"]
        )
        if not recoverable:
            raise BatchConflict("batch has no recoverable items")
        self._schedule(batch_id)
        return public(document)

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.store.list(limit)


def _experiment_state(phase: str) -> str:
    if phase in {"select-image", "image-selected"}:
        return "awaiting_review"
    if phase in {"complete"}:
        return "succeeded"
    if phase in {"image-generation-failed", "asset3d-failed"}:
        return "failed"
    return "running"


def _job_state(state: str) -> str:
    if state in {"planned", "submitting", "queued", "running"}:
        return "running"
    if state in {"uncertain", "connection_required"}:
        return "uncertain"
    return state
