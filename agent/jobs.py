from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from builtins import TimeoutError as BuiltinTimeoutError
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import modal
from modal.exception import OutputExpiredError
from modal.exception import TimeoutError as ModalTimeoutError

from agent.modal_client import client
from agent.storage import data_dir

_TERMINAL = {"succeeded", "failed", "cancelled", "expired"}


def default_db_path() -> Path:
    return data_dir() / "jobs.sqlite3"


@dataclass
class Job:
    id: str
    model: str
    remote_call_id: str
    status: str
    created_at: str
    result: dict | None = None
    error: str | None = None

    def public(self) -> dict:
        return {
            "id": self.id,
            "model": self.model,
            "status": self.status,
            "created_at": self.created_at,
            "result": self.result,
            "error": self.error,
        }


class JobManager:
    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or default_db_path()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()
        self._initialize_db()
        self._load()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path, timeout=5)

    def _initialize_db(self) -> None:
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    model TEXT NOT NULL,
                    remote_call_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    result_json TEXT,
                    error TEXT
                )
                """
            )

    def _load(self) -> None:
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, model, remote_call_id, status, created_at, result_json, error FROM jobs"
            ).fetchall()
        with self._lock:
            self._jobs = {
                row[0]: Job(
                    id=row[0],
                    model=row[1],
                    remote_call_id=row[2],
                    status=row[3],
                    created_at=row[4],
                    result=json.loads(row[5]) if row[5] else None,
                    error=row[6],
                )
                for row in rows
            }

    def _save(self, job: Job) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO jobs (id, model, remote_call_id, status, created_at, result_json, error)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    model=excluded.model,
                    remote_call_id=excluded.remote_call_id,
                    status=excluded.status,
                    result_json=excluded.result_json,
                    error=excluded.error
                """,
                (
                    job.id,
                    job.model,
                    job.remote_call_id,
                    job.status,
                    job.created_at,
                    json.dumps(job.result, separators=(",", ":")) if job.result is not None else None,
                    job.error,
                ),
            )

    def create(self, model: str, remote_call_id: str) -> dict:
        job = Job(
            id=uuid.uuid4().hex,
            model=model,
            remote_call_id=remote_call_id,
            status="running",
            created_at=datetime.now(UTC).isoformat(),
        )
        with self._lock:
            self._jobs[job.id] = job
            self._save(job)
        return job.public()

    def list(self, limit: int = 50) -> list[dict]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda job: job.created_at, reverse=True)
            return [job.public() for job in jobs[:limit]]

    def _get(self, job_id: str) -> Job:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    def poll(self, job_id: str) -> dict:
        job = self._get(job_id)
        if job.status in _TERMINAL:
            return job.public()

        call = modal.FunctionCall.from_id(job.remote_call_id, client=client())
        status: str
        result: dict | None = None
        error: str | None = None
        try:
            result = call.get(timeout=0)
        except (ModalTimeoutError, BuiltinTimeoutError):
            with self._lock:
                return self._jobs[job_id].public()
        except OutputExpiredError:
            status = "expired"
            error = "远程任务结果已过期"
        except Exception as exc:  # noqa: BLE001 - remote workers may raise model-specific exceptions.
            status = "failed"
            error = str(exc) or type(exc).__name__
        else:
            status = "succeeded"

        with self._lock:
            current = self._jobs[job_id]
            if current.status in _TERMINAL:
                return current.public()
            current.status = status
            current.result = result
            current.error = error
            self._save(current)
            return current.public()

    def cancel(self, job_id: str) -> dict:
        job = self._get(job_id)
        if job.status in _TERMINAL:
            return job.public()

        modal.FunctionCall.from_id(job.remote_call_id, client=client()).cancel()
        with self._lock:
            current = self._jobs[job_id]
            if current.status not in _TERMINAL:
                current.status = "cancelled"
                self._save(current)
            return current.public()


jobs = JobManager()
