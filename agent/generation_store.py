from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

from agent.statuses import PROJECT_REMOTE_ACTIVE_STATUSES


class GenerationConflict(ValueError):
    pass


class GenerationSubmissionUnknown(RuntimeError):
    pass


@dataclass(frozen=True)
class GenerationIntent:
    request_id: str
    project_id: str
    canonical_sha256: str
    model: str
    profile: str
    state: str
    remote_call_id: str | None
    job_id: str | None
    error: str | None
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "GenerationIntent":
        return cls(**dict(row))

    def public(self) -> dict:
        return {
            "request_id": self.request_id,
            "project_id": self.project_id,
            "canonical_sha256": self.canonical_sha256,
            "model": self.model,
            "profile": self.profile,
            "state": self.state,
            "remote_call_id": self.remote_call_id,
            "job_id": self.job_id,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _now() -> str:
    return datetime.now(UTC).isoformat()


class GenerationIntentStore:
    """持久化一次生成提交的本地意图。

    远端调用和 SQLite 无法组成分布式事务，因此对“已调用远端但尚未拿到/落库
    call_id”的崩溃窗口采用 fail-closed：重启后进入 uncertain，而不是自动释放后
    再次提交。只要 remote_call_id 已落库，就可以继续重建 Job 并完成绑定。
    """

    _UNCERTAIN_MESSAGE = (
        "上次生成提交在确认远端任务前中断。为避免重复计费，项目已暂停再次提交。"
    )

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._initialize_db()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.db_path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _initialize_db(self) -> None:
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS generation_intents (
                    request_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    canonical_sha256 TEXT NOT NULL,
                    model TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    state TEXT NOT NULL,
                    remote_call_id TEXT,
                    job_id TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            db.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS generation_intents_project_active
                ON generation_intents(project_id)
                WHERE state IN ('preparing', 'submitting', 'remote_created', 'uncertain')
                """
            )

    @staticmethod
    def _intent(row: sqlite3.Row) -> GenerationIntent:
        return GenerationIntent.from_row(row)

    @staticmethod
    def _validate_replay(
        intent: GenerationIntent,
        project_id: str,
        canonical_sha256: str,
        model: str,
        profile: str,
    ) -> None:
        expected = (project_id, canonical_sha256, model, profile)
        actual = (intent.project_id, intent.canonical_sha256, intent.model, intent.profile)
        if actual != expected:
            raise GenerationConflict("request_id 已用于不同的生成参数")

    def get(self, request_id: str) -> dict:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM generation_intents WHERE request_id = ?", (request_id,)
            ).fetchone()
        if row is None:
            raise KeyError(request_id)
        return self._intent(row).public()

    def claim(self, project_id: str, request_id: str, model: str, profile: str) -> dict:
        now = _now()
        active = tuple(PROJECT_REMOTE_ACTIVE_STATUSES)
        placeholders = ", ".join("?" for _ in active)
        with self._connect() as db:
            project = db.execute(
                "SELECT canonical_sha256 FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            if project is None:
                raise KeyError(project_id)
            canonical_sha256 = project[0]
            if not canonical_sha256:
                raise RuntimeError("项目尚无本地 Canonical RGBA")

            history = db.execute(
                """
                SELECT job_id, canonical_sha256, model, profile
                FROM project_generations
                WHERE project_id = ? AND request_id = ?
                """,
                (project_id, request_id),
            ).fetchone()
            if history is not None:
                if (history[1], history[2], history[3]) != (canonical_sha256, model, profile):
                    raise GenerationConflict("request_id 已用于不同的生成参数")
                return {"claimed": False, "job_id": history[0], "state": "bound"}

            row = db.execute(
                "SELECT * FROM generation_intents WHERE request_id = ?", (request_id,)
            ).fetchone()
            if row is not None:
                intent = self._intent(row)
                self._validate_replay(intent, project_id, canonical_sha256, model, profile)
                if intent.state == "abandoned":
                    raise GenerationConflict("该 request_id 已放弃，请使用新的 request_id")
                if intent.state == "uncertain":
                    raise GenerationSubmissionUnknown(intent.error or self._UNCERTAIN_MESSAGE)
                return {
                    "claimed": False,
                    "job_id": intent.job_id,
                    "state": intent.state,
                }

            cursor = db.execute(
                f"""
                UPDATE projects
                SET status = 'submitting', job_id = NULL, error = NULL, updated_at = ?
                WHERE id = ?
                  AND status NOT IN ({placeholders})
                  AND canonical_id IS NOT NULL
                  AND canonical_sha256 IS NOT NULL
                  AND canonical_bytes IS NOT NULL
                """,
                (now, project_id, *active),
            )
            if cursor.rowcount != 1:
                latest = db.execute(
                    "SELECT status, canonical_sha256 FROM projects WHERE id = ?", (project_id,)
                ).fetchone()
                if latest is None:
                    raise KeyError(project_id)
                replay = db.execute(
                    "SELECT * FROM generation_intents WHERE request_id = ?", (request_id,)
                ).fetchone()
                if replay is not None:
                    intent = self._intent(replay)
                    self._validate_replay(intent, project_id, latest[1], model, profile)
                    if intent.state == "abandoned":
                        raise GenerationConflict("该 request_id 已放弃，请使用新的 request_id")
                    if intent.state == "uncertain":
                        raise GenerationSubmissionUnknown(intent.error or self._UNCERTAIN_MESSAGE)
                    return {"claimed": False, "job_id": intent.job_id, "state": intent.state}
                if latest[0] in PROJECT_REMOTE_ACTIVE_STATUSES:
                    raise GenerationConflict("该项目已有远程生成任务正在活动")
                raise RuntimeError("项目尚无本地 Canonical RGBA")

            try:
                db.execute(
                    """
                    INSERT INTO generation_intents (
                        request_id, project_id, canonical_sha256, model, profile, state,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'preparing', ?, ?)
                    """,
                    (request_id, project_id, canonical_sha256, model, profile, now, now),
                )
            except sqlite3.IntegrityError as exc:
                raise GenerationConflict("该项目已有远程生成任务正在活动") from exc
            return {"claimed": True, "job_id": None, "state": "preparing"}

    def begin_remote(self, request_id: str) -> dict:
        """标记即将进入可能产生远端副作用的调用边界。"""
        now = _now()
        with self._connect() as db:
            cursor = db.execute(
                """
                UPDATE generation_intents
                SET state = 'submitting', error = NULL, updated_at = ?
                WHERE request_id = ? AND state = 'preparing'
                """,
                (now, request_id),
            )
            if cursor.rowcount != 1:
                row = db.execute(
                    "SELECT state FROM generation_intents WHERE request_id = ?",
                    (request_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(request_id)
                if row[0] != "submitting":
                    raise GenerationConflict("生成提交状态已被其它操作改变")
        return self.get(request_id)

    def mark_remote(self, request_id: str, remote_call_id: str) -> dict:
        now = _now()
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM generation_intents WHERE request_id = ?", (request_id,)
            ).fetchone()
            if row is None:
                raise KeyError(request_id)
            intent = self._intent(row)
            if intent.remote_call_id and intent.remote_call_id != remote_call_id:
                raise GenerationConflict("同一生成请求绑定了不同的远端任务")
            if intent.state == "bound":
                return intent.public()
            db.execute(
                """
                UPDATE generation_intents
                SET state = 'remote_created', remote_call_id = ?, error = NULL, updated_at = ?
                WHERE request_id = ?
                """,
                (remote_call_id, now, request_id),
            )
        return self.get(request_id)

    def bind_job(self, request_id: str, job_id: str) -> dict:
        """原子写入 generation history、Project 摘要与 intent 绑定。"""
        now = _now()
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM generation_intents WHERE request_id = ?", (request_id,)
            ).fetchone()
            if row is None:
                raise KeyError(request_id)
            intent = self._intent(row)
            if not intent.remote_call_id:
                raise RuntimeError("远端任务尚未持久化")
            if intent.job_id and intent.job_id != job_id:
                raise GenerationConflict("同一生成请求绑定了不同的本地 Job")
            if intent.state == "bound":
                return intent.public()

            existing = db.execute(
                "SELECT job_id FROM project_generations WHERE project_id = ? AND request_id = ?",
                (intent.project_id, request_id),
            ).fetchone()
            if existing is not None and existing[0] != job_id:
                raise GenerationConflict("同一生成请求绑定了不同的本地 Job")
            if existing is None:
                generation_id = f"gen_{request_id}"
                db.execute(
                    """
                    INSERT INTO project_generations (
                        id, project_id, canonical_sha256, model, profile, job_id, request_id,
                        status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?, ?)
                    """,
                    (
                        generation_id, intent.project_id, intent.canonical_sha256, intent.model,
                        intent.profile, job_id, request_id, now, now,
                    ),
                )

            project = db.execute(
                """
                UPDATE projects SET
                    model = ?, profile = ?, job_id = ?, artifact_path = NULL,
                    artifact_id = NULL, artifact_sha256 = NULL, artifact_bytes = NULL,
                    artifact_canonical_sha256 = ?, status = 'generating', error = NULL,
                    updated_at = ?
                WHERE id = ? AND (
                    status IN ('submitting', 'submission_unknown') OR job_id = ?
                )
                """,
                (
                    intent.model, intent.profile, job_id, intent.canonical_sha256, now,
                    intent.project_id, job_id,
                ),
            )
            if project.rowcount != 1:
                raise GenerationConflict("项目生成状态已被其它操作改变")

            db.execute(
                """
                UPDATE generation_intents
                SET state = 'bound', job_id = ?, error = NULL, updated_at = ?
                WHERE request_id = ?
                """,
                (job_id, now, request_id),
            )
        return self.get(request_id)

    def mark_uncertain(self, request_id: str, error: str | None = None) -> None:
        message = error or self._UNCERTAIN_MESSAGE
        now = _now()
        with self._connect() as db:
            row = db.execute(
                "SELECT project_id, state FROM generation_intents WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if row is None:
                raise KeyError(request_id)
            if row[1] == "bound":
                return
            db.execute(
                """
                UPDATE generation_intents
                SET state = 'uncertain', error = ?, updated_at = ?
                WHERE request_id = ?
                """,
                (message, now, request_id),
            )
            db.execute(
                """
                UPDATE projects SET status = 'submission_unknown', error = ?, updated_at = ?
                WHERE id = ? AND status = 'submitting'
                """,
                (message, now, row[0]),
            )

    def release_pre_remote(self, request_id: str) -> None:
        """只释放尚未触发远端调用的意图。"""
        now = _now()
        with self._connect() as db:
            row = db.execute(
                "SELECT project_id, state FROM generation_intents WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if row is None:
                return
            if row[1] != "preparing":
                raise RuntimeError("远端提交开始后不能自动释放生成意图")
            db.execute("DELETE FROM generation_intents WHERE request_id = ?", (request_id,))
            db.execute(
                """
                UPDATE projects
                SET status = CASE
                    WHEN artifact_id IS NOT NULL AND artifact_canonical_sha256 = canonical_sha256
                    THEN 'succeeded' ELSE 'ready' END,
                    error = NULL, updated_at = ?
                WHERE id = ? AND status = 'submitting'
                """,
                (now, row[0]),
            )

    def abandon_uncertain(self, project_id: str) -> dict:
        """显式放弃无法确认的远端提交；调用方必须提示潜在重复计费风险。"""
        now = _now()
        with self._connect() as db:
            project = db.execute(
                "SELECT status FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            if project is None:
                raise KeyError(project_id)
            if project[0] != "submission_unknown":
                raise GenerationConflict("项目当前没有待确认的生成提交")

            intent = db.execute(
                """
                SELECT request_id, state FROM generation_intents
                WHERE project_id = ? AND state = 'uncertain'
                """,
                (project_id,),
            ).fetchone()
            if intent is not None:
                db.execute(
                    """
                    UPDATE generation_intents
                    SET state = 'abandoned', updated_at = ?
                    WHERE request_id = ? AND state = 'uncertain'
                    """,
                    (now, intent[0]),
                )

            db.execute(
                """
                UPDATE projects
                SET status = CASE
                    WHEN artifact_id IS NOT NULL AND artifact_canonical_sha256 = canonical_sha256
                    THEN 'succeeded' ELSE 'ready' END,
                    error = NULL, updated_at = ?
                WHERE id = ?
                """,
                (now, project_id),
            )
        return {"project_id": project_id}

    def recover_after_restart(self) -> list[dict]:
        """恢复安全准备态，并关闭真正不确定的远端提交窗口。"""
        now = _now()
        message = self._UNCERTAIN_MESSAGE
        with self._connect() as db:
            db.execute(
                """
                UPDATE projects
                SET status = CASE
                    WHEN artifact_id IS NOT NULL AND artifact_canonical_sha256 = canonical_sha256
                    THEN 'succeeded' ELSE 'ready' END,
                    error = NULL, updated_at = ?
                WHERE status = 'submitting'
                  AND id IN (
                      SELECT project_id FROM generation_intents WHERE state = 'preparing'
                  )
                """,
                (now,),
            )
            db.execute("DELETE FROM generation_intents WHERE state = 'preparing'")
            db.execute(
                """
                UPDATE generation_intents
                SET state = 'uncertain', error = ?, updated_at = ?
                WHERE state = 'submitting'
                """,
                (message, now),
            )
            db.execute(
                """
                UPDATE projects
                SET status = 'submission_unknown', error = ?, updated_at = ?
                WHERE id IN (
                    SELECT project_id FROM generation_intents WHERE state = 'uncertain'
                ) AND status = 'submitting'
                """,
                (message, now),
            )
            # 兼容上一版本仅在 projects.generation_request_id 中保存占位的数据库。
            db.execute(
                """
                UPDATE projects
                SET status = 'submission_unknown', error = ?, updated_at = ?
                WHERE status = 'submitting'
                  AND NOT EXISTS (
                      SELECT 1 FROM generation_intents gi WHERE gi.project_id = projects.id
                  )
                """,
                (message, now),
            )
            rows = db.execute(
                """
                SELECT * FROM generation_intents
                WHERE state = 'remote_created'
                ORDER BY created_at
                """
            ).fetchall()
        return [self._intent(row).public() for row in rows]
