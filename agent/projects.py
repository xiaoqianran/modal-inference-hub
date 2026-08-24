from __future__ import annotations

import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agent.storage import data_dir

_MAX_SOURCE_BYTES = 25 * 1024 * 1024
_ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


@dataclass
class Project:
    id: str
    title: str
    source_name: str
    source_path: str
    source_bytes: int
    concept: str | None
    sam_provider: str | None
    scene_id: str | None
    selection_id: str | None
    candidate_id: str | None
    canonical_path: str | None
    canonical_bytes: int | None
    model: str | None
    profile: str | None
    job_id: str | None
    artifact_path: str | None
    artifact_bytes: int | None
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
            "concept": self.concept,
            "sam_provider": self.sam_provider,
            "scene_id": self.scene_id,
            "selection_id": self.selection_id,
            "candidate_id": self.candidate_id,
            "canonical_path": self.canonical_path,
            "canonical_bytes": self.canonical_bytes,
            "model": self.model,
            "profile": self.profile,
            "job_id": self.job_id,
            "artifact_path": self.artifact_path,
            "artifact_bytes": self.artifact_bytes,
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

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

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
                    concept TEXT,
                    sam_provider TEXT,
                    scene_id TEXT,
                    selection_id TEXT,
                    candidate_id TEXT,
                    canonical_path TEXT,
                    canonical_bytes INTEGER,
                    model TEXT,
                    profile TEXT,
                    job_id TEXT,
                    artifact_path TEXT,
                    artifact_bytes INTEGER,
                    status TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(projects)")}
            if "sam_provider" not in columns:
                db.execute("ALTER TABLE projects ADD COLUMN sam_provider TEXT")

    @staticmethod
    def _project(row: sqlite3.Row) -> Project:
        return Project(**dict(row))

    def create(self, data: bytes, filename: str) -> dict:
        if not data:
            raise ValueError("图片为空")
        if len(data) > _MAX_SOURCE_BYTES:
            raise ValueError("图片不能超过 25 MiB")
        name = Path(filename or "source.png").name
        suffix = Path(name).suffix.lower()
        if suffix not in _ALLOWED_SUFFIXES:
            raise ValueError("只支持 PNG、JPEG 和 WebP 图片")

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
                    id, title, source_name, source_path, source_bytes, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'draft', ?, ?)
                """,
                (project_id, title, name, str(source), len(data), now, now),
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

    def delete(self, project_id: str) -> dict:
        project = self.get(project_id)
        if project["status"] in {"generating", "running", "connection_required", "cancel_requested"}:
            raise ValueError("项目仍有远程任务活动，请先等待终态或完成取消")

        directory = self.assets / project_id
        tombstone = self.assets / f".delete-{project_id}-{uuid.uuid4().hex}"
        moved = False
        if directory.is_dir():
            directory.replace(tombstone)
            moved = True
        try:
            with self._connect() as db:
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

    def record_segmentation(
        self, project_id: str, concept: str, provider: str, selection: dict
    ) -> dict:
        return self._update(
            project_id,
            concept=concept,
            sam_provider=provider,
            scene_id=selection["scene_id"],
            selection_id=selection["selection_id"],
            candidate_id=None,
            canonical_path=None,
            canonical_bytes=None,
            job_id=None,
            artifact_path=None,
            artifact_bytes=None,
            status="segmented",
            error=None,
        )

    def record_canonical(self, project_id: str, candidate_id: str, canonical: dict) -> dict:
        return self._update(
            project_id,
            candidate_id=candidate_id,
            canonical_path=canonical["canonical_path"],
            canonical_bytes=canonical["canonical_bytes"],
            job_id=None,
            artifact_path=None,
            artifact_bytes=None,
            status="ready",
            error=None,
        )

    def record_generation(self, project_id: str, model: str, profile: str, job_id: str) -> dict:
        return self._update(
            project_id,
            model=model,
            profile=profile,
            job_id=job_id,
            artifact_path=None,
            artifact_bytes=None,
            status="generating",
            error=None,
        )

    def record_job(self, job: dict) -> None:
        with self._connect() as db:
            row = db.execute("SELECT id FROM projects WHERE job_id = ?", (job["id"],)).fetchone()
        if row is None:
            return
        status = job["status"]
        result = job.get("result")
        artifact = result.get("artifact") if result else None
        self._update(
            row[0],
            artifact_path=artifact.get("path") if artifact else None,
            artifact_bytes=artifact.get("bytes") if artifact else None,
            status=status,
            error=job.get("error"),
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
