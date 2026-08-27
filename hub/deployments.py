"""Vertical slice: human-confirmed deployment of provider-owned release plans.

Pure document transitions live above a small SQLite/subprocess shell.  The Hub
knows how to locate a Provider deployer, but the Provider owns every Modal app,
command and verification rule behind its JSON event boundary.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol


class DeploymentError(RuntimeError):
    pass


class DeploymentNotFound(DeploymentError):
    pass


class DeploymentConflict(DeploymentError):
    pass


def now() -> str:
    return datetime.now(UTC).isoformat()


def new_deployment(deployment_id: str, provider: str, timestamp: str) -> dict[str, Any]:
    return {
        "id": deployment_id,
        "provider": provider,
        "state": "queued",
        "stage": "queued",
        "events": [],
        "result": None,
        "error": None,
        "createdAt": timestamp,
        "updatedAt": timestamp,
    }


def record_event(
    document: dict[str, Any], event: dict[str, Any], timestamp: str
) -> dict[str, Any]:
    stage = str(event.get("stage") or "unknown")
    state = str(event.get("state") or "running")
    if state not in {"running", "succeeded", "failed"}:
        state = "running"
    updated = copy.deepcopy(document)
    public_event = {
        "stage": stage,
        "state": state,
        "message": str(event.get("message") or "")[:500],
        "at": timestamp,
    }
    updated["events"].append(public_event)
    updated["events"] = updated["events"][-100:]
    updated["stage"] = stage
    updated["state"] = (
        "failed" if state == "failed" else "succeeded" if stage == "complete" else "running"
    )
    if isinstance(event.get("result"), dict):
        updated["result"] = copy.deepcopy(event["result"])
    if state == "failed":
        updated["error"] = public_event["message"] or "Provider 部署失败"
    updated["updatedAt"] = timestamp
    return updated


class DeploymentStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS deployments (
                    id TEXT PRIMARY KEY,
                    document_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            rows = db.execute("SELECT id, document_json FROM deployments").fetchall()
            for deployment_id, payload in rows:
                document = json.loads(payload)
                if document.get("state") not in {"queued", "running"}:
                    continue
                document = record_event(
                    document,
                    {
                        "stage": "interrupted",
                        "state": "failed",
                        "message": "Hub 在部署期间退出；请先检查 Provider 状态再重新部署",
                    },
                    now(),
                )
                self._write(db, deployment_id, document)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _write(db: sqlite3.Connection, deployment_id: str, document: dict[str, Any]) -> None:
        db.execute(
            "UPDATE deployments SET document_json = ?, updated_at = ? WHERE id = ?",
            (
                json.dumps(document, ensure_ascii=False, separators=(",", ":")),
                document["updatedAt"],
                deployment_id,
            ),
        )

    def create(self, document: dict[str, Any]) -> dict[str, Any]:
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO deployments VALUES (?, ?, ?, ?)",
                (
                    document["id"],
                    json.dumps(document, ensure_ascii=False, separators=(",", ":")),
                    document["createdAt"],
                    document["updatedAt"],
                ),
            )
        return copy.deepcopy(document)

    def get(self, deployment_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute(
                "SELECT document_json FROM deployments WHERE id = ?", (deployment_id,)
            ).fetchone()
        if row is None:
            raise DeploymentNotFound("deployment not found")
        return json.loads(row[0])

    def append(self, deployment_id: str, event: dict[str, Any]) -> dict[str, Any]:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT document_json FROM deployments WHERE id = ?", (deployment_id,)
            ).fetchone()
            if row is None:
                raise DeploymentNotFound("deployment not found")
            document = record_event(json.loads(row[0]), event, now())
            self._write(db, deployment_id, document)
        return document

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT document_json FROM deployments ORDER BY updated_at DESC LIMIT ?",
                (max(1, min(limit, 100)),),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]


class Deployer(Protocol):
    def plan(self) -> dict[str, Any]: ...

    def apply(
        self, token_id: str, token_secret: str, on_event: Callable[[dict[str, Any]], None]
    ) -> None: ...


_TOKEN = re.compile(r"\b(?:ak|as)-[A-Za-z0-9_-]+")


@dataclass(frozen=True, slots=True)
class ProviderDeployer:
    provider: str
    repository: Path
    module: str

    def _command(self, action: str) -> list[str]:
        uv = shutil.which("uv")
        if not uv:
            raise DeploymentError("未找到 uv，无法启动 Provider 部署器")
        if not self.repository.is_dir():
            raise DeploymentError(f"未找到 {self.provider} Provider 源码")
        return [uv, "run", "python", "-m", f"{self.module}.deployment", action]

    def plan(self) -> dict[str, Any]:
        completed = subprocess.run(
            self._command("plan"),
            cwd=self.repository,
            check=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        if completed.returncode != 0:
            raise DeploymentError(f"{self.provider} 部署计划不可用")
        for line in reversed(completed.stdout.splitlines()):
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and value.get("provider") == self.provider:
                return value
        raise DeploymentError(f"{self.provider} 部署器返回了无效计划")

    def apply(
        self, token_id: str, token_secret: str, on_event: Callable[[dict[str, Any]], None]
    ) -> None:
        environment = {
            **os.environ,
            "MODAL_TOKEN_ID": token_id,
            "MODAL_TOKEN_SECRET": token_secret,
            "PYTHONUNBUFFERED": "1",
        }
        process = subprocess.Popen(
            self._command("apply"),
            cwd=self.repository,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert process.stdout is not None
        failed_event = False
        for line in process.stdout:
            safe = _TOKEN.sub("***", line)
            safe = safe.replace(token_id, "***").replace(token_secret, "***")
            try:
                value = json.loads(safe)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                failed_event = failed_event or value.get("state") == "failed"
                on_event(value)
        return_code = process.wait()
        if return_code != 0 and not failed_event:
            raise DeploymentError(f"{self.provider} 部署器退出，code={return_code}")


def _workspace() -> Path:
    configured = os.environ.get("MODAL_HUB_WORKSPACE")
    if configured:
        return Path(configured)
    origins = (Path.cwd(), Path(sys.executable).resolve().parent, Path(__file__).resolve().parent)
    for origin in origins:
        for candidate in (origin, *origin.parents):
            if (candidate / "modal-2D").is_dir() and (candidate / "modal-3D").is_dir():
                return candidate
    return Path.cwd()


def default_deployers() -> dict[str, ProviderDeployer]:
    workspace = _workspace()
    return {
        "modal-2d": ProviderDeployer(
            "modal-2d",
            Path(os.environ.get("MODAL_2D_PROVIDER_REPO", workspace / "modal-2D")),
            "modal_2d",
        ),
        "modal-3d": ProviderDeployer(
            "modal-3d",
            Path(os.environ.get("MODAL_3D_PROVIDER_REPO", workspace / "modal-3D")),
            "modal_3d",
        ),
    }


Background = Callable[[Callable[[], None]], None]


def _thread(task: Callable[[], None]) -> None:
    threading.Thread(target=task, daemon=True, name="provider-deployment").start()


@dataclass(slots=True)
class DeploymentService:
    store: DeploymentStore
    deployers: dict[str, Deployer]
    background: Background = _thread
    _lock: threading.RLock = field(init=False, repr=False)
    _active: set[str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._lock = threading.RLock()
        self._active: set[str] = set()

    def _deployer(self, provider: str) -> Deployer:
        try:
            return self.deployers[provider]
        except KeyError as exc:
            raise DeploymentNotFound("provider deployer not found") from exc

    def plan(self, provider: str) -> dict[str, Any]:
        return self._deployer(provider).plan()

    def start(self, provider: str, token_id: str, token_secret: str) -> dict[str, Any]:
        with self._lock:
            if provider in self._active:
                raise DeploymentConflict(f"{provider} already has an active deployment")
            self._active.add(provider)
        deployment_id = f"dep_{uuid.uuid4().hex}"
        document = self.store.create(new_deployment(deployment_id, provider, now()))

        def execute() -> None:
            try:
                self._deployer(provider).apply(
                    token_id,
                    token_secret,
                    lambda event: self.store.append(deployment_id, event),
                )
                current = self.store.get(deployment_id)
                if current["state"] not in {"succeeded", "failed"}:
                    self.store.append(
                        deployment_id,
                        {
                            "stage": "complete",
                            "state": "succeeded",
                            "message": "Provider 部署完成",
                        },
                    )
            except Exception:  # noqa: BLE001 - redact every subprocess/SDK failure at this shell.
                self.store.append(
                    deployment_id,
                    {"stage": "failed", "state": "failed", "message": "Provider 部署失败"},
                )
            finally:
                with self._lock:
                    self._active.discard(provider)

        self.background(execute)
        return document

    def get(self, deployment_id: str) -> dict[str, Any]:
        return self.store.get(deployment_id)

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.store.list(limit)
