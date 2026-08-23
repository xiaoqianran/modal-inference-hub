from __future__ import annotations

import threading
import uuid
from builtins import TimeoutError as BuiltinTimeoutError
from dataclasses import dataclass
from datetime import UTC, datetime

import modal
from modal.exception import OutputExpiredError
from modal.exception import TimeoutError as ModalTimeoutError

from agent.modal_client import client

_TERMINAL = {"succeeded", "failed", "cancelled", "expired"}


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
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()

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
        return job.public()

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
        try:
            result = call.get(timeout=0)
        except (ModalTimeoutError, BuiltinTimeoutError):
            return job.public()
        except OutputExpiredError:
            job.status = "expired"
            job.error = "远程任务结果已过期"
        except Exception as exc:  # noqa: BLE001 - remote workers may raise model-specific exceptions.
            job.status = "failed"
            job.error = str(exc) or type(exc).__name__
        else:
            job.status = "succeeded"
            job.result = result
        return job.public()

    def cancel(self, job_id: str) -> dict:
        job = self._get(job_id)
        if job.status not in _TERMINAL:
            modal.FunctionCall.from_id(job.remote_call_id, client=client()).cancel()
            job.status = "cancelled"
        return job.public()


jobs = JobManager()
