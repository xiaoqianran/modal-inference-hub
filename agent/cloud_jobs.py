from __future__ import annotations

import threading
import uuid

from agent.cloud import generation

_lock = threading.Lock()
_jobs: dict[str, dict] = {}


def create(model: str, input_path: str, options: dict | None = None) -> dict:
    submitted = generation.submit(model, input_path, options)
    job_id = uuid.uuid4().hex
    with _lock:
        _jobs[job_id] = {"call_id": submitted["call_id"], "model": submitted.get("model", model)}
    return {"job_id": job_id, "model": submitted.get("model", model), "status": "pending"}


def get(job_id: str) -> dict:
    with _lock:
        job = _jobs.get(job_id)
    if job is None:
        raise FileNotFoundError(f"generation job not found: {job_id}")
    remote = generation.result(job["call_id"])
    status = remote.get("status")
    if status == "done":
        return {"job_id": job_id, "model": job["model"], "status": "succeeded", "result": remote.get("result")}
    if status == "expired":
        return {"job_id": job_id, "model": job["model"], "status": "expired"}
    return {"job_id": job_id, "model": job["model"], "status": "pending"}
