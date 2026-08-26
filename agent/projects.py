"""项目（Project）工作区的持久化。

Project 承载一次完整创作流程的全部上下文：源图片 → 本地 rembg/组件选择 →
Canonical RGBA → 模型/Profile → Job → 产物。全部落盘到 SQLite，支持
Agent 重启后恢复。状态推进见下方状态机图；Job 状态由 jobs.record_job 回写。
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

from agent import image_input
from agent.statuses import PROJECT_REMOTE_ACTIVE_STATUSES
from agent.storage import data_dir

# Project 状态机（随工作流逐步推进）：
#
#   draft ──(local rembg + canonicalize)──► ready ──(generation)──► generating
#                                                                              │
#                                       ┌──────────────────────────────────────┘
#                                       │  （由 jobs.py 的 Job 状态回写，见 record_job）
#                                       ▼
#                    running / connection_required / cancel_requested ──► succeeded / failed /
#                                                                          cancelled / expired
#
# 其中 draft / segmented / ready 是「本地可安全删除」的中间态；
# generating / running / connection_required / cancel_requested 表示仍有远程任务活动，
# 删除项目前必须先让 Job 到达终态或完成取消（见 delete）。


@dataclass
class Project:
    id: str
    title: str
    source_name: str
    source_path: str
    source_bytes: int
    source_id: str | None
    source_sha256: str | None
    source_mime: str | None
    source_width: int | None
    source_height: int | None
    canonical_path: str | None
    canonical_remote_sha256: str | None
    canonical_id: str | None
    canonical_sha256: str | None
    canonical_bytes: int | None
    model: str | None
    profile: str | None
    job_id: str | None
    artifact_path: str | None
    artifact_id: str | None
    artifact_sha256: str | None
    artifact_bytes: int | None
    artifact_canonical_sha256: str | None
    status: str
    error: str | None
    created_at: str
    updated_at: str

    def public(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "source_name": self.source_name,
            "source_bytes": self.source_bytes,
            "source": (
                {
                    "id": self.source_id,
                    "role": "source-image",
                    "mime": self.source_mime,
                    "bytes": self.source_bytes,
                    "sha256": self.source_sha256,
                    "width": self.source_width,
                    "height": self.source_height,
                }
                if self.source_id
                else None
            ),
            "canonical_id": self.canonical_id,
            "canonical_sha256": self.canonical_sha256,
            "canonical_bytes": self.canonical_bytes,
            "model": self.model,
            "profile": self.profile,
            "job_id": self.job_id,
            "artifact_id": self.artifact_id,
            "artifact_sha256": self.artifact_sha256,
            "artifact_bytes": self.artifact_bytes,
            "artifact_canonical_sha256": self.artifact_canonical_sha256,
            "status": self.status,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class ProjectStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or data_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        self.assets = self.root / "projects"
        self.assets.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "projects.sqlite3"
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
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    source_bytes INTEGER NOT NULL,
                    source_id TEXT,
                    source_sha256 TEXT,
                    source_mime TEXT,
                    source_width INTEGER,
                    source_height INTEGER,
                    canonical_path TEXT,
                    canonical_remote_sha256 TEXT,
                    canonical_id TEXT,
                    canonical_sha256 TEXT,
                    canonical_bytes INTEGER,
                    model TEXT,
                    profile TEXT,
                    job_id TEXT,
                    artifact_path TEXT,
                    artifact_id TEXT,
                    artifact_sha256 TEXT,
                    artifact_bytes INTEGER,
                    artifact_canonical_sha256 TEXT,
                    status TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(projects)")}
            migrations = {
                "artifact_id": "TEXT",
                "artifact_sha256": "TEXT",
                "source_id": "TEXT",
                "source_sha256": "TEXT",
                "source_mime": "TEXT",
                "source_width": "INTEGER",
                "source_height": "INTEGER",
                "canonical_id": "TEXT",
                "canonical_sha256": "TEXT",
                "canonical_remote_sha256": "TEXT",
                "artifact_canonical_sha256": "TEXT",
            }
            for name, definition in migrations.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE projects ADD COLUMN {name} {definition}")
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS project_generations (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    canonical_sha256 TEXT NOT NULL,
                    model TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    job_id TEXT NOT NULL UNIQUE,
                    request_id TEXT,
                    artifact_id TEXT,
                    artifact_sha256 TEXT,
                    artifact_bytes INTEGER,
                    status TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            generation_columns = {
                row[1] for row in db.execute("PRAGMA table_info(project_generations)")
            }
            if "request_id" not in generation_columns:
                db.execute("ALTER TABLE project_generations ADD COLUMN request_id TEXT")
            db.execute(
                "CREATE INDEX IF NOT EXISTS project_generations_project_updated "
                "ON project_generations(project_id, updated_at DESC)"
            )
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS project_generations_project_request "
                "ON project_generations(project_id, request_id) WHERE request_id IS NOT NULL"
            )
            # 将旧版 projects 表中唯一的生成结果迁移为第一条成果记录。
            legacy = db.execute(
                """
                SELECT id, canonical_sha256, artifact_canonical_sha256, model, profile,
                       job_id, artifact_id, artifact_sha256, artifact_bytes, status,
                       error, created_at, updated_at
                FROM projects WHERE job_id IS NOT NULL
                """
            ).fetchall()
            for row in legacy:
                status = "succeeded" if row[6] else row[9]
                db.execute(
                    """
                    INSERT OR IGNORE INTO project_generations (
                        id, project_id, canonical_sha256, model, profile, job_id,
                        artifact_id, artifact_sha256, artifact_bytes, status, error,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"gen_{row[5]}", row[0], row[2] or row[1] or "unknown",
                        row[3] or "unknown", row[4] or "recommended", row[5],
                        row[6], row[7], row[8], status, row[10], row[11], row[12],
                    ),
                )

    @staticmethod
    def _project(row: sqlite3.Row) -> Project:
        # 旧版本数据库可能仍包含已经退役的 SAM 字段。只读取当前 Project
        # 模型声明的列即可，既不继续传播死字段，也无需破坏性 DROP COLUMN。
        current_fields = {item.name for item in fields(Project)}
        return Project(**{key: value for key, value in dict(row).items() if key in current_fields})

    def create(self, data: bytes, filename: str, limits: dict | None = None) -> dict:
        name = Path(filename or "source.png").name
        descriptor = image_input.describe(data, name, limits)
        suffix = Path(name).suffix.lower()

        project_id = uuid.uuid4().hex
        directory = self.assets / project_id
        directory.mkdir(parents=True, exist_ok=False)
        source = directory / f"source{suffix}"
        source.write_bytes(data)
        now = datetime.now(UTC).isoformat()
        title = Path(name).stem[:80] or "未命名项目"
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO projects (
                    id, title, source_name, source_path, source_bytes,
                    source_id, source_sha256, source_mime, source_width, source_height,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)
                """,
                (
                    project_id, title, name, str(source), len(data),
                    descriptor["id"], descriptor["sha256"], descriptor["mime"],
                    descriptor["width"], descriptor["height"], now, now,
                ),
            )
        return self.get(project_id)

    def get(self, project_id: str) -> dict:
        with self._connect() as db:
            row = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if row is None:
            raise KeyError(project_id)
        return self._project(row).public()

    def list(self, limit: int = 20) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM projects ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._project(row).public() for row in rows]

    def list_generations(self, project_id: str, limit: int = 50) -> list[dict]:
        self.get(project_id)
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT id, project_id, canonical_sha256, model, profile, job_id,
                       artifact_id, artifact_sha256, artifact_bytes, status, error,
                       created_at, updated_at
                FROM project_generations
                WHERE project_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (project_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete(self, project_id: str) -> dict:
        project = self.get(project_id)
        if project["status"] in PROJECT_REMOTE_ACTIVE_STATUSES:
            raise ValueError("项目仍有远程任务活动，请先等待终态或完成取消")

        directory = self.assets / project_id
        tombstone = self.assets / f".delete-{project_id}-{uuid.uuid4().hex}"
        moved = False
        if directory.is_dir():
            directory.replace(tombstone)
            moved = True
        try:
            with self._connect() as db:
                db.execute("DELETE FROM project_generations WHERE project_id = ?", (project_id,))
                cursor = db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
                if cursor.rowcount != 1:
                    raise KeyError(project_id)
        except Exception:
            if moved and tombstone.is_dir():
                tombstone.replace(directory)
            raise
        if moved:
            shutil.rmtree(tombstone, ignore_errors=True)
        return project

    def source_path(self, project_id: str) -> Path:
        with self._connect() as db:
            row = db.execute("SELECT source_path FROM projects WHERE id = ?", (project_id,)).fetchone()
        if row is None:
            raise KeyError(project_id)
        path = Path(row[0])
        if not path.is_file():
            raise FileNotFoundError(path)
        return path

    def source_bytes(self, project_id: str) -> bytes:
        return self.source_path(project_id).read_bytes()

    def _asset_path(self, project_id: str, name: str) -> Path:
        self.get(project_id)
        directory = self.assets / project_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory / name

    def matte_path(self, project_id: str) -> Path:
        path = self._asset_path(project_id, "matte.png")
        if not path.is_file():
            raise FileNotFoundError(path)
        return path

    def selection_path(self, project_id: str) -> Path:
        path = self._asset_path(project_id, "selection.png")
        if not path.is_file():
            raise FileNotFoundError(path)
        return path

    def save_selection_preview(self, project_id: str, data: bytes) -> Path:
        path = self._asset_path(project_id, "selection.png")
        temporary = path.with_suffix(".png.tmp")
        temporary.write_bytes(data)
        temporary.replace(path)
        return path

    def canonical_local_path(self, project_id: str) -> Path:
        path = self._asset_path(project_id, "canonical.png")
        if not path.is_file():
            raise FileNotFoundError(path)
        return path

    def component_state_path(self, project_id: str) -> Path:
        return self._asset_path(project_id, "components.json")

    def component_state(self, project_id: str) -> dict:
        path = self.component_state_path(project_id)
        if not path.is_file():
            raise FileNotFoundError(path)
        return json.loads(path.read_text(encoding="utf-8"))

    def save_component_state(self, project_id: str, state: dict) -> None:
        path = self.component_state_path(project_id)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def save_preprocessed(
        self,
        project_id: str,
        matte_bytes: bytes,
        canonical_bytes: bytes,
        descriptor: dict,
        component_state: dict | None = None,
    ) -> dict:
        paths = (
            (self._asset_path(project_id, "matte.png"), matte_bytes),
            (self._asset_path(project_id, "selection.png"), matte_bytes),
            (self._asset_path(project_id, "canonical.png"), canonical_bytes),
        )
        for path, data in paths:
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_bytes(data)
            temporary.replace(path)
        if component_state is not None:
            self.save_component_state(project_id, component_state)
        return self.record_local_canonical(project_id, descriptor)

    def save_canonical_selection(
        self,
        project_id: str,
        selection_bytes: bytes,
        canonical_bytes: bytes,
        descriptor: dict,
        component_state: dict,
    ) -> dict:
        self.save_selection_preview(project_id, selection_bytes)
        path = self._asset_path(project_id, "canonical.png")
        temporary = path.with_suffix(".png.tmp")
        temporary.write_bytes(canonical_bytes)
        temporary.replace(path)
        self.save_component_state(project_id, component_state)
        return self.record_local_canonical(project_id, descriptor)

    def record_local_canonical(self, project_id: str, canonical: dict) -> dict:
        """更新 Canonical；活动中的远端 generation 生命周期保持不变。"""
        active = tuple(PROJECT_REMOTE_ACTIVE_STATUSES)
        placeholders = ", ".join("?" for _ in active)
        now = datetime.now(UTC).isoformat()
        with self._connect() as db:
            cursor = db.execute(
                f"""
                UPDATE projects SET
                    canonical_path = NULL,
                    canonical_remote_sha256 = NULL,
                    canonical_id = ?,
                    canonical_sha256 = ?,
                    canonical_bytes = ?,
                    status = CASE WHEN status IN ({placeholders}) THEN status ELSE 'ready' END,
                    error = CASE WHEN status IN ({placeholders}) THEN error ELSE NULL END,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    canonical["id"], canonical["sha256"], canonical["bytes"],
                    *active, *active, now, project_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(project_id)
        return self.get(project_id)

    def canonical_descriptor(self, project_id: str) -> dict:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT canonical_id, canonical_sha256, canonical_bytes
                FROM projects WHERE id = ?
                """,
                (project_id,),
            ).fetchone()
        if row is None:
            raise KeyError(project_id)
        if not all((row[0], row[1], row[2])):
            raise RuntimeError("项目尚无本地 Canonical RGBA")
        return {
            "id": row[0],
            "role": "canonical-rgba",
            "mime": "image/png",
            "bytes": row[2],
            "sha256": row[1],
            "width": 1024,
            "height": 1024,
            "mode": "RGBA",
        }

    def canonical_local(self, project_id: str) -> tuple[dict, Path]:
        return self.canonical_descriptor(project_id), self.canonical_local_path(project_id)

    def canonical_remote(self, project_id: str) -> tuple[dict, str]:
        descriptor = self.canonical_descriptor(project_id)
        with self._connect() as db:
            row = db.execute(
                "SELECT canonical_path, canonical_remote_sha256 FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
        if row is None:
            raise KeyError(project_id)
        if not row[0] or not row[1]:
            raise RuntimeError("Canonical RGBA 尚未上传到 Modal Volume")
        if str(row[1]) != descriptor["sha256"]:
            raise RuntimeError("远端 Canonical 与当前本地 Canonical 不一致，需要重新上传")
        return descriptor, str(row[0])

    def record_remote_canonical(
        self, project_id: str, remote_path: str, canonical_sha256: str
    ) -> dict:
        descriptor = self.canonical_descriptor(project_id)
        if canonical_sha256 != descriptor["sha256"]:
            raise ValueError("远端 Canonical SHA256 与当前本地 Canonical 不一致")
        return self._update(
            project_id,
            canonical_path=remote_path,
            canonical_remote_sha256=canonical_sha256,
        )

    def record_job(self, job: dict) -> None:
        with self._connect() as db:
            generation = db.execute(
                "SELECT project_id FROM project_generations WHERE job_id = ?", (job["id"],)
            ).fetchone()
            if generation is None:
                return
            status = job["status"]
            result = job.get("result")
            artifact = result.get("artifact") if result else None
            artifact_id = artifact.get("id") if artifact else None
            artifact_sha256 = artifact.get("sha256") if artifact else None
            artifact_bytes = artifact.get("bytes") if artifact else None
            now = datetime.now(UTC).isoformat()
            db.execute(
                """
                UPDATE project_generations SET
                    artifact_id = ?, artifact_sha256 = ?, artifact_bytes = ?,
                    status = ?, error = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (artifact_id, artifact_sha256, artifact_bytes, status, job.get("error"), now, job["id"]),
            )
            # 只有项目当前绑定的任务可以改变项目摘要；历史成果轮询不会覆盖新任务。
            db.execute(
                """
                UPDATE projects SET
                    artifact_path = NULL, artifact_id = ?, artifact_sha256 = ?,
                    artifact_bytes = ?, status = ?, error = ?, updated_at = ?
                WHERE id = ? AND job_id = ?
                """,
                (
                    artifact_id, artifact_sha256, artifact_bytes, status,
                    job.get("error"), now, generation[0], job["id"],
                ),
            )

    def _update(self, project_id: str, **values) -> dict:
        if not values:
            return self.get(project_id)
        values["updated_at"] = datetime.now(UTC).isoformat()
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self._connect() as db:
            cursor = db.execute(
                f"UPDATE projects SET {assignments} WHERE id = ?",
                (*values.values(), project_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(project_id)
        return self.get(project_id)


projects = ProjectStore()
