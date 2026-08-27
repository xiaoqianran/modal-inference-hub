"""3D 生成任务（Job）的编排与可恢复持久化。

Job 是「云端 modal.FunctionCall」的本地镜像：本地只存 remote_call_id，
状态推进依赖 poll() 轮询远端。关键设计：

1. 可恢复：连接类错误 → connection_required，重连后 poll() 自动回到 running；
2. 产物校验：远端成功返回后，先经 artifacts.cache_remote 落盘校验，通过才置 succeeded；
3. 幂等恢复：Agent 重启后从 SQLite 读回所有 Job，未终态者继续轮询；
   已成功但本地缓存被清理者，可凭 artifact_remote_path 重新拉取。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from builtins import TimeoutError as BuiltinTimeoutError
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

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

from agent import artifacts
from agent.modal_client import NotConnectedError, client
from agent.statuses import JOB_TERMINAL_STATUSES
from agent.storage import data_dir

_DB_VERSION = 3
_REMOTE_NOT_FOUND_CONFIRMATIONS = 2
# Modal may keep an input PENDING while repeatedly retrying a container that can
# never deserialize/start. The remote 30-minute function timeout does not always
# terminate that pre-execution retry loop, so the desktop needs its own wall clock.
_REMOTE_JOB_MAX_WALL_SECONDS = 45 * 60
_AUTH_ERRORS = (NotConnectedError, AuthError, PermissionDeniedError)
_TRANSIENT_ERRORS = (ModalConnectionError, InternalError, ServiceError, ResourceExhaustedError)
_RECOVERABLE_ERRORS = (*_AUTH_ERRORS, *_TRANSIENT_ERRORS, ModalTimeoutError, BuiltinTimeoutError)
_CANCELLED_REMOTE_MESSAGE = "function call was cancelled"

# Job 状态机（含可恢复语义）：
#
#   running ──────────────────────────────► succeeded / failed / expired
#      │                                        （终态）
#      │  连接中断（认证/网络/服务不可用）
#      └──────────────► connection_required ──► running（重连成功后自动恢复轮询）
#      │
#      │  用户请求取消
#      └──────────────► cancel_requested ──► cancelled（远端确认）
#                                │
#                                └────────► expired（远端结果已过期，无法取消）
#
# 说明：connection_required 与 cancel_requested 均为「可恢复」的中间态，
# 远端 FunctionCall 可能仍在运行，重连后继续轮询即可。


def default_db_path() -> Path:
    return data_dir() / "jobs.sqlite3"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _job_age_seconds(job: "Job") -> float:
    try:
        created = datetime.fromisoformat(job.created_at)
    except ValueError:
        return 0.0
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return max(0.0, (datetime.now(UTC) - created.astimezone(UTC)).total_seconds())


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
    artifact_remote_path: str | None = None
    remote_not_found_count: int = 0

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

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self._db_path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

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
                    retryable INTEGER,
                    artifact_remote_path TEXT,
                    remote_not_found_count INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
            for name, definition in (
                ("updated_at", "TEXT"),
                ("error_code", "TEXT"),
                ("retryable", "INTEGER"),
                ("artifact_remote_path", "TEXT"),
                ("remote_not_found_count", "INTEGER NOT NULL DEFAULT 0"),
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
                       result_json, error, error_code, retryable, artifact_remote_path,
                       remote_not_found_count
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
                    artifact_remote_path=row[10],
                    remote_not_found_count=int(row[11] or 0),
                )
                for row in rows
            }

    def _save(self, job: Job) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO jobs (
                    id, model, remote_call_id, status, created_at, updated_at,
                    result_json, error, error_code, retryable, artifact_remote_path,
                    remote_not_found_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    model=excluded.model,
                    remote_call_id=excluded.remote_call_id,
                    status=excluded.status,
                    updated_at=excluded.updated_at,
                    result_json=excluded.result_json,
                    error=excluded.error,
                    error_code=excluded.error_code,
                    retryable=excluded.retryable,
                    artifact_remote_path=excluded.artifact_remote_path,
                    remote_not_found_count=excluded.remote_not_found_count
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
                    job.artifact_remote_path,
                    job.remote_not_found_count,
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
        artifact_remote_path: str | None = None,
    ) -> dict:
        with self._lock:
            job = self._jobs[job_id]
            if job.status in JOB_TERMINAL_STATUSES:
                return job.public()
            job.status = status
            job.updated_at = _now()
            job.result = result
            job.error = error
            job.error_code = error_code
            job.retryable = retryable
            if artifact_remote_path is not None:
                job.artifact_remote_path = artifact_remote_path
            self._save(job)
            return job.public()

    def _reset_remote_not_found(self, job: Job) -> None:
        if job.remote_not_found_count == 0:
            return
        with self._lock:
            current = self._jobs[job.id]
            current.remote_not_found_count = 0
            self._save(current)

    def _not_found_state(self, job: Job) -> dict:
        with self._lock:
            current = self._jobs[job.id]
            current.remote_not_found_count += 1
            self._save(current)
            count = current.remote_not_found_count
        if count >= _REMOTE_NOT_FOUND_CONFIRMATIONS:
            return self._set_state(
                job.id,
                "expired",
                error="远程任务已连续确认不可用或结果已过期",
                error_code="remote.output_expired",
                retryable=False,
            )
        return self._set_state(
            job.id,
            "cancel_requested" if job.status == "cancel_requested" else "connection_required",
            error="暂时无法确认远程任务，稍后将再次检查",
            error_code="remote.lookup_uncertain",
            retryable=True,
        )

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

    def create(self, model: str, remote_call_id: str, *, job_id: str | None = None) -> dict:
        """按 remote_call_id 幂等创建本地 Job；调用方可提供稳定 Job ID 用于崩溃恢复。"""
        if job_id is not None and (not job_id or len(job_id) > 128):
            raise ValueError("无效的本地 Job ID")
        with self._lock:
            by_id = self._jobs.get(job_id) if job_id is not None else None
            if by_id is not None:
                if by_id.model != model or by_id.remote_call_id != remote_call_id:
                    raise ValueError("本地 Job ID 已绑定不同远端任务")
                return by_id.public()

            existing = next(
                (job for job in self._jobs.values() if job.remote_call_id == remote_call_id),
                None,
            )
            if existing is not None:
                if existing.model != model:
                    raise ValueError("同一远端任务不能绑定不同模型")
                if job_id is not None and existing.id != job_id:
                    raise ValueError("远端任务已绑定其它本地 Job")
                return existing.public()

            timestamp = _now()
            job = Job(
                id=job_id or uuid.uuid4().hex,
                model=model,
                remote_call_id=remote_call_id,
                status="running",
                created_at=timestamp,
                updated_at=timestamp,
            )
            self._jobs[job.id] = job
            self._save(job)
            return job.public()

    def list(self, limit: int = 50) -> list[dict]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda job: job.created_at, reverse=True)
            return [job.public() for job in jobs[:limit]]

    def get(self, job_id: str) -> dict:
        return self._get(job_id).public()

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
            return self._not_found_state(job)
        except Exception:  # noqa: BLE001 - cancellation acknowledgement may fail independently.
            return self._set_state(
                job.id,
                "cancel_requested",
                error="取消请求尚未确认，正在继续等待远端状态",
                error_code="cancel.request_pending",
                retryable=True,
            )
        current = self._get(job.id)
        if current.status in JOB_TERMINAL_STATUSES:
            return current.public()
        if current.error is None and current.error_code is None and current.retryable is True:
            return current.public()
        return self._set_state(current.id, "cancel_requested", retryable=True)

    def poll(self, job_id: str) -> dict:
        job = self._get(job_id)
        if job.status in JOB_TERMINAL_STATUSES:
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
            return self._not_found_state(job)
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
            self._reset_remote_not_found(current)
            if current.status == "cancel_requested" and call is not None:
                return self._retry_cancel(current, call)
            if _job_age_seconds(current) >= _REMOTE_JOB_MAX_WALL_SECONDS:
                if call is not None:
                    try:
                        call.cancel()
                    except Exception:
                        # Local terminal state must not depend on Modal acknowledging
                        # cancellation of a call already stuck in startup retries.
                        pass
                return self._set_state(
                    current.id,
                    "failed",
                    error=(
                        "云端任务超过 45 分钟仍未进入终态，已停止等待。"
                        "请先确认模型部署为最新版本后重新生成"
                    ),
                    error_code="remote.stalled",
                    retryable=True,
                )
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
            self._reset_remote_not_found(job)
            try:
                public_result, remote_path = self._cache_result(job, result)
            except artifacts.ArtifactValidationError as exc:
                return self._set_state(
                    job.id,
                    "failed",
                    error=f"3D 产物校验失败：{exc}",
                    error_code="artifact.validation_failed",
                    retryable=False,
                )
            except NotFoundError:
                return self._set_state(
                    job.id,
                    "failed",
                    error="3D 产物在远端不存在或已过期",
                    error_code="artifact.missing",
                    retryable=False,
                )
            except _RECOVERABLE_ERRORS as exc:
                return self._connection_state(self._get(job.id), exc)
            return self._set_state(
                job.id,
                "succeeded",
                result=public_result,
                retryable=False,
                artifact_remote_path=remote_path,
            )

    def _cache_result(self, job: Job, result) -> tuple[dict, str]:
        if not isinstance(result, dict):
            raise artifacts.ArtifactValidationError("generation result 必须是对象")
        raw_artifact = result.get("artifact")
        if not isinstance(raw_artifact, dict):
            raise artifacts.ArtifactValidationError("generation result 缺少 artifact descriptor")
        remote_path = raw_artifact.get("path")
        if not isinstance(remote_path, str) or not remote_path:
            raise artifacts.ArtifactValidationError("generation result 缺少内部 artifact path")
        descriptor, _ = artifacts.cache_remote(remote_path, raw_artifact, job.model)
        public_result = {key: value for key, value in result.items() if key != "artifact"}
        public_result["artifact"] = descriptor
        public_result["primary_artifact_id"] = result.get("primary_artifact_id") or descriptor["id"]
        raw_artifacts = result.get("artifacts")
        if isinstance(raw_artifacts, list):
            public_result["artifacts"] = [
                {
                    key: value
                    for key, value in item.items()
                    if key not in {"path", "remote_path", "internal_path"}
                }
                for item in raw_artifacts
                if isinstance(item, dict)
            ]
        else:
            public_result["artifacts"] = [descriptor]
        return public_result, artifacts.normalize_path(remote_path)

    def artifact(self, job_id: str) -> tuple[dict, Path]:
        """返回已验证产物（descriptor, 本地缓存路径）。

        若本地缓存已被清理，则凭 artifact_remote_path 从远端重新拉取并回填。
        """
        job = self._get(job_id)
        if job.status != "succeeded" or not isinstance(job.result, dict):
            raise RuntimeError("任务尚无可用的已验证产物")
        descriptor = job.result.get("artifact")
        if not isinstance(descriptor, dict):
            raise artifacts.ArtifactValidationError("任务缺少 artifact descriptor")
        try:
            path = artifacts.verified_path(descriptor)
        except FileNotFoundError:
            if not job.artifact_remote_path:
                raise
            descriptor, path = artifacts.cache_remote(
                job.artifact_remote_path, descriptor, job.model
            )
            job.result["artifact"] = descriptor
            self._save(job)
        return descriptor, path

    def cancel(self, job_id: str) -> dict:
        job = self._get(job_id)
        if job.status in JOB_TERMINAL_STATUSES:
            return job.public()

        self._set_state(job.id, "cancel_requested", retryable=True)
        current = self._get(job.id)
        try:
            call = modal.FunctionCall.from_id(current.remote_call_id, client=client())
        except _RECOVERABLE_ERRORS as exc:
            return self._connection_state(current, exc)
        except NotFoundError:
            return self._not_found_state(current)
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
