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
from modal.exception import (
    AuthError,
    FunctionTimeoutError,
    InternalError,
    NotFoundError,
    OutputExpiredError,
    PermissionDeniedError,
    RemoteError,
    ResourceExhaustedError,
    ServiceError,
)
from modal.exception import ConnectionError as ModalConnectionError
from modal.exception import TimeoutError as ModalTimeoutError

from agent.modal_client import NotConnectedError, client
from agent.storage import data_dir

_DB_VERSION = 1
_TERMINAL = {"succeeded", "failed", "cancelled", "expired"}
_AUTH_ERRORS = (NotConnectedError, AuthError, PermissionDeniedError)
_TRANSIENT_ERRORS = (ModalConnectionError, InternalError, ServiceError, ResourceExhaustedError)
_RECOVERABLE_ERRORS = (*_AUTH_ERRORS, *_TRANSIENT_ERRORS)
_CANCELLED_REMOTE_MESSAGE = "function call was cancelled"


def default_db_path() -> Path:
    return data_dir() / "jobs.sqlite3"


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class Job:
    id: str
    model: str
    remote_call_id: str
    status: str
    created_at: str
    updated_at: str
    result: dict | None = None
    error: str | None = None
    error_code: str | None = None
    retryable: bool | None = None

    def public(self) -> dict:
        return {
            "id": self.id,
            "model": self.model,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "result": self.result,
            "error": self.error,
            "error_code": self.error_code,
            "retryable": self.retryable,
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
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > _DB_VERSION:
                raise RuntimeError(f"Job DB 版本过新：{version} > {_DB_VERSION}")
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    model TEXT NOT NULL,
                    remote_call_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT,
                    result_json TEXT,
                    error TEXT,
                    error_code TEXT,
                    retryable INTEGER
                )
                """
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
            for name, definition in (
                ("updated_at", "TEXT"),
                ("error_code", "TEXT"),
                ("retryable", "INTEGER"),
            ):
                if name not in columns:
                    db.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")
            db.execute("UPDATE jobs SET updated_at = created_at WHERE updated_at IS NULL")
            db.execute(f"PRAGMA user_version = {_DB_VERSION}")

    def _load(self) -> None:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT id, model, remote_call_id, status, created_at, updated_at,
                       result_json, error, error_code, retryable
                FROM jobs
                """
            ).fetchall()
        with self._lock:
            self._jobs = {
                row[0]: Job(
                    id=row[0],
                    model=row[1],
                    remote_call_id=row[2],
                    status=row[3],
                    created_at=row[4],
                    updated_at=row[5] or row[4],
                    result=json.loads(row[6]) if row[6] else None,
                    error=row[7],
                    error_code=row[8],
                    retryable=None if row[9] is None else bool(row[9]),
                )
                for row in rows
            }

    def _save(self, job: Job) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO jobs (
                    id, model, remote_call_id, status, created_at, updated_at,
                    result_json, error, error_code, retryable
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    model=excluded.model,
                    remote_call_id=excluded.remote_call_id,
                    status=excluded.status,
                    updated_at=excluded.updated_at,
                    result_json=excluded.result_json,
                    error=excluded.error,
                    error_code=excluded.error_code,
                    retryable=excluded.retryable
                """,
                (
                    job.id,
                    job.model,
                    job.remote_call_id,
                    job.status,
                    job.created_at,
                    job.updated_at,
                    json.dumps(job.result, separators=(",", ":")) if job.result is not None else None,
                    job.error,
                    job.error_code,
                    None if job.retryable is None else int(job.retryable),
                ),
            )

    def _set_state(
        self,
        job_id: str,
        status: str,
        *,
        result: dict | None = None,
        error: str | None = None,
        error_code: str | None = None,
        retryable: bool | None = None,
    ) -> dict:
        with self._lock:
            job = self._jobs[job_id]
            if job.status in _TERMINAL:
                return job.public()
            job.status = status
            job.updated_at = _now()
            job.result = result
            job.error = error
            job.error_code = error_code
            job.retryable = retryable
            self._save(job)
            return job.public()

    def _connection_state(self, job: Job, exc: BaseException) -> dict:
        cancel_pending = job.status == "cancel_requested"
        if isinstance(exc, _AUTH_ERRORS):
            code = "modal.auth_required"
            message = "云端连接需要重新认证，远端任务可能仍在运行"
        else:
            code = "modal.connection_unavailable"
            message = "云端连接暂时不可用，远端任务可能仍在运行"
        return self._set_state(
            job.id,
            "cancel_requested" if cancel_pending else "connection_required",
            error=message,
            error_code=code,
            retryable=True,
        )

    def create(self, model: str, remote_call_id: str) -> dict:
        timestamp = _now()
        job = Job(
            id=uuid.uuid4().hex,
            model=model,
            remote_call_id=remote_call_id,
            status="running",
            created_at=timestamp,
            updated_at=timestamp,
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

    def _retry_cancel(self, job: Job, call: modal.FunctionCall) -> dict:
        try:
            call.cancel()
        except _RECOVERABLE_ERRORS as exc:
            return self._connection_state(job, exc)
        except NotFoundError:
            return self._set_state(
                job.id,
                "expired",
                error="远程任务结果已过期",
                error_code="remote.output_expired",
                retryable=False,
            )
        except Exception:  # noqa: BLE001 - cancellation acknowledgement may fail independently.
            return self._set_state(
                job.id,
                "cancel_requested",
                error="取消请求尚未确认，正在继续等待远端状态",
                error_code="cancel.request_pending",
                retryable=True,
            )
        current = self._get(job.id)
        if current.status in _TERMINAL:
            return current.public()
        if current.error is None and current.error_code is None and current.retryable is True:
            return current.public()
        return self._set_state(current.id, "cancel_requested", retryable=True)

    def poll(self, job_id: str) -> dict:
        job = self._get(job_id)
        if job.status in _TERMINAL:
            return job.public()

        call: modal.FunctionCall | None = None
        try:
            call = modal.FunctionCall.from_id(job.remote_call_id, client=client())
            result = call.get(timeout=0)
        except OutputExpiredError:
            return self._set_state(
                job.id,
                "expired",
                error="远程任务结果已过期",
                error_code="remote.output_expired",
                retryable=False,
            )
        except NotFoundError:
            return self._set_state(
                job.id,
                "expired",
                error="远程任务已不可用或结果已过期",
                error_code="remote.output_expired",
                retryable=False,
            )
        except FunctionTimeoutError:
            return self._set_state(
                job.id,
                "failed",
                error="云端模型执行超时",
                error_code="remote.execution_timeout",
                retryable=False,
            )
        except (ModalTimeoutError, BuiltinTimeoutError):
            current = self._get(job.id)
            if current.status == "cancel_requested" and call is not None:
                return self._retry_cancel(current, call)
            if current.status == "connection_required":
                return self._set_state(job.id, "running")
            return current.public()
        except RemoteError as exc:
            current = self._get(job.id)
            if current.status == "cancel_requested" and _CANCELLED_REMOTE_MESSAGE in str(exc).lower():
                return self._set_state(job.id, "cancelled", retryable=False)
            return self._set_state(
                job.id,
                "failed",
                error="云端模型执行失败：RemoteError",
                error_code="remote.execution_failed",
                retryable=False,
            )
        except _RECOVERABLE_ERRORS as exc:
            return self._connection_state(self._get(job.id), exc)
        except Exception as exc:  # noqa: BLE001 - remote workers may raise model-specific exceptions.
            return self._set_state(
                job.id,
                "failed",
                error=f"云端模型执行失败：{type(exc).__name__}",
                error_code="remote.execution_failed",
                retryable=False,
            )
        else:
            return self._set_state(job.id, "succeeded", result=result, retryable=False)

    def cancel(self, job_id: str) -> dict:
        job = self._get(job_id)
        if job.status in _TERMINAL:
            return job.public()

        self._set_state(job.id, "cancel_requested", retryable=True)
        current = self._get(job.id)
        try:
            call = modal.FunctionCall.from_id(current.remote_call_id, client=client())
        except _RECOVERABLE_ERRORS as exc:
            return self._connection_state(current, exc)
        except NotFoundError:
            return self._set_state(
                current.id,
                "expired",
                error="远程任务已不可用或结果已过期",
                error_code="remote.output_expired",
                retryable=False,
            )
        except Exception:  # noqa: BLE001 - keep cancellation intent recoverable on unknown SDK errors.
            return self._set_state(
                current.id,
                "cancel_requested",
                error="取消请求尚未确认，正在继续等待远端状态",
                error_code="cancel.request_pending",
                retryable=True,
            )
        return self._retry_cancel(current, call)


jobs = JobManager()
