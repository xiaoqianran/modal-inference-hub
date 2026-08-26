from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from modal.exception import (
    AuthError,
    ConnectionError,
    FunctionTimeoutError,
    NotFoundError,
    OutputExpiredError,
    RemoteError,
)

from agent.artifacts import ArtifactValidationError
from agent.jobs import JobManager
from agent.modal_client import NotConnectedError


class JobManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "jobs.sqlite3"
        self.manager = JobManager(self.db)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def create(self) -> dict:
        return self.manager.create("fastsam3d-plus-plus", "fc-test")

    def test_create_is_idempotent_by_remote_call_id(self) -> None:
        first = self.create()
        second = self.manager.create("fastsam3d-plus-plus", "fc-test")

        self.assertEqual(second["id"], first["id"])
        self.assertEqual(len(self.manager.list()), 1)

    def test_create_rejects_remote_call_model_mismatch(self) -> None:
        self.create()
        with self.assertRaisesRegex(ValueError, "不同模型"):
            self.manager.create("other-model", "fc-test")

    def poll_with(self, job_id: str, *, result=None, error: BaseException | None = None):
        call = Mock()
        if error is not None:
            call.get.side_effect = error
        else:
            call.get.return_value = result
        with (
            patch("agent.jobs.client", return_value=Mock()),
            patch("agent.jobs.modal.FunctionCall.from_id", return_value=call),
        ):
            value = self.manager.poll(job_id)
        return value, call

    def test_pending_poll_stays_running(self) -> None:
        job = self.create()
        value, _ = self.poll_with(job["id"], error=TimeoutError())
        self.assertEqual(value["status"], "running")
        self.assertIsNone(value["error_code"])

    def test_connection_error_is_recoverable(self) -> None:
        job = self.create()
        value, _ = self.poll_with(job["id"], error=ConnectionError("offline"))
        self.assertEqual(value["status"], "connection_required")
        self.assertEqual(value["error_code"], "modal.connection_unavailable")
        self.assertTrue(value["retryable"])

        value, _ = self.poll_with(job["id"], error=TimeoutError())
        self.assertEqual(value["status"], "running")
        self.assertIsNone(value["error_code"])

    def test_not_connected_is_recoverable(self) -> None:
        job = self.create()
        with patch("agent.jobs.client", side_effect=NotConnectedError("disconnected")):
            value = self.manager.poll(job["id"])
        self.assertEqual(value["status"], "connection_required")
        self.assertEqual(value["error_code"], "modal.auth_required")
        self.assertTrue(value["retryable"])

    def test_auth_error_is_recoverable(self) -> None:
        job = self.create()
        value, _ = self.poll_with(job["id"], error=AuthError("bad token"))
        self.assertEqual(value["status"], "connection_required")
        self.assertEqual(value["error_code"], "modal.auth_required")
        self.assertTrue(value["retryable"])

    def test_remote_function_timeout_is_terminal(self) -> None:
        job = self.create()
        value, _ = self.poll_with(job["id"], error=FunctionTimeoutError("worker timeout"))
        self.assertEqual(value["status"], "failed")
        self.assertEqual(value["error_code"], "remote.execution_timeout")
        self.assertFalse(value["retryable"])

    def test_remote_exception_is_terminal_and_sanitized(self) -> None:
        job = self.create()
        value, _ = self.poll_with(job["id"], error=ValueError("secret traceback detail"))
        self.assertEqual(value["status"], "failed")
        self.assertEqual(value["error_code"], "remote.execution_failed")
        self.assertNotIn("secret traceback detail", value["error"])

    def test_not_found_requires_confirmation_before_expiring(self) -> None:
        job = self.create()
        value, _ = self.poll_with(job["id"], error=NotFoundError())
        self.assertEqual(value["status"], "connection_required")
        self.assertEqual(value["error_code"], "remote.lookup_uncertain")
        self.assertTrue(value["retryable"])

        value, _ = self.poll_with(job["id"], error=NotFoundError())
        self.assertEqual(value["status"], "expired")
        self.assertEqual(value["error_code"], "remote.output_expired")

    def test_not_found_confirmation_survives_manager_restart(self) -> None:
        job = self.create()
        value, _ = self.poll_with(job["id"], error=NotFoundError())
        self.assertEqual(value["status"], "connection_required")

        restarted = JobManager(self.db)
        call = Mock()
        call.get.side_effect = NotFoundError()
        with (
            patch("agent.jobs.client", return_value=Mock()),
            patch("agent.jobs.modal.FunctionCall.from_id", return_value=call),
        ):
            value = restarted.poll(job["id"])

        self.assertEqual(value["status"], "expired")
        self.assertEqual(value["error_code"], "remote.output_expired")

    def test_pending_remote_resets_not_found_confirmation(self) -> None:
        job = self.create()
        value, _ = self.poll_with(job["id"], error=NotFoundError())
        self.assertEqual(value["status"], "connection_required")

        value, _ = self.poll_with(job["id"], error=TimeoutError())
        self.assertEqual(value["status"], "running")

        value, _ = self.poll_with(job["id"], error=NotFoundError())
        self.assertEqual(value["status"], "connection_required")
        self.assertEqual(value["error_code"], "remote.lookup_uncertain")

    def test_output_expired_is_terminal(self) -> None:
        job = self.create()
        value, _ = self.poll_with(job["id"], error=OutputExpiredError())
        self.assertEqual(value["status"], "expired")
        self.assertEqual(value["error_code"], "remote.output_expired")

    def test_cancel_race_can_still_succeed(self) -> None:
        job = self.create()
        call = Mock()
        with (
            patch("agent.jobs.client", return_value=Mock()),
            patch("agent.jobs.modal.FunctionCall.from_id", return_value=call),
        ):
            requested = self.manager.cancel(job["id"])
        self.assertEqual(requested["status"], "cancel_requested")

        descriptor = {
            "id": "art-test",
            "role": "primary-glb",
            "mime": "model/gltf-binary",
            "bytes": 12,
            "sha256": "a" * 64,
        }
        with patch(
            "agent.jobs.artifacts.cache_remote",
            return_value=(descriptor, Path(self.temp.name) / "cached.glb"),
        ):
            value, _ = self.poll_with(job["id"], result={"artifact": {"path": "x.glb"}})
        self.assertEqual(value["status"], "succeeded")
        self.assertEqual(value["result"]["artifact"], descriptor)
        self.assertNotIn("path", value["result"]["artifact"])

    def test_success_is_persisted_only_after_verified_cache(self) -> None:
        job = self.create()
        descriptor = {
            "id": "art-verified",
            "role": "primary-glb",
            "mime": "model/gltf-binary",
            "bytes": 128,
            "sha256": "b" * 64,
        }
        remote = {
            "model": "fastsam3d-plus-plus",
            "artifact": {"path": "jobs/private.glb", "bytes": 128},
            "timing": {"inference_s": 1.0},
        }
        cached = Path(self.temp.name) / "cache.glb"
        with patch("agent.jobs.artifacts.cache_remote", return_value=(descriptor, cached)) as cache:
            value, _ = self.poll_with(job["id"], result=remote)
        self.assertEqual(value["status"], "succeeded")
        self.assertEqual(value["result"]["primary_artifact_id"], "art-verified")
        self.assertNotIn("path", value["result"]["artifact"])
        cache.assert_called_once()
        with closing(sqlite3.connect(self.db)) as db:
            stored = db.execute(
                "SELECT artifact_remote_path, result_json FROM jobs WHERE id = ?", (job["id"],)
            ).fetchone()
        self.assertEqual(stored[0], "jobs/private.glb")
        self.assertNotIn("jobs/private.glb", stored[1])

    def test_artifact_validation_failure_is_terminal(self) -> None:
        job = self.create()
        with patch(
            "agent.jobs.artifacts.cache_remote",
            side_effect=ArtifactValidationError("hash mismatch"),
        ):
            value, _ = self.poll_with(
                job["id"], result={"artifact": {"path": "jobs/bad.glb"}}
            )
        self.assertEqual(value["status"], "failed")
        self.assertEqual(value["error_code"], "artifact.validation_failed")

    def test_verified_terminal_result_survives_offline_reload(self) -> None:
        job = self.create()
        descriptor = {
            "id": "art-offline",
            "role": "primary-glb",
            "mime": "model/gltf-binary",
            "bytes": 12,
            "sha256": "c" * 64,
        }
        cached = Path(self.temp.name) / "cache.glb"
        with patch("agent.jobs.artifacts.cache_remote", return_value=(descriptor, cached)):
            value, _ = self.poll_with(
                job["id"], result={"artifact": {"path": "jobs/offline.glb"}}
            )
        self.assertEqual(value["status"], "succeeded")
        restored = JobManager(self.db)
        with patch("agent.jobs.artifacts.verified_path", return_value=cached):
            restored_descriptor, restored_path = restored.artifact(job["id"])
        self.assertEqual(restored_descriptor["id"], "art-offline")
        self.assertEqual(restored_path, cached)

    def test_cancel_confirmation_becomes_cancelled(self) -> None:
        job = self.create()
        call = Mock()
        with (
            patch("agent.jobs.client", return_value=Mock()),
            patch("agent.jobs.modal.FunctionCall.from_id", return_value=call),
        ):
            self.manager.cancel(job["id"])

        value, _ = self.poll_with(
            job["id"],
            error=RemoteError("Function call was cancelled by user or a failure."),
        )
        self.assertEqual(value["status"], "cancelled")
        self.assertFalse(value["retryable"])

    def test_cancel_pending_retries_cancel(self) -> None:
        job = self.create()
        call = Mock()
        with (
            patch("agent.jobs.client", return_value=Mock()),
            patch("agent.jobs.modal.FunctionCall.from_id", return_value=call),
        ):
            self.manager.cancel(job["id"])
        call.reset_mock()
        call.get.side_effect = TimeoutError()
        with (
            patch("agent.jobs.client", return_value=Mock()),
            patch("agent.jobs.modal.FunctionCall.from_id", return_value=call),
        ):
            value = self.manager.poll(job["id"])
        self.assertEqual(value["status"], "cancel_requested")
        call.cancel.assert_called_once()

    def test_legacy_database_migrates_in_place(self) -> None:
        legacy = Path(self.temp.name) / "legacy.sqlite3"
        with closing(sqlite3.connect(legacy)) as db, db:
            db.execute(
                """
                CREATE TABLE jobs (
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
            db.execute(
                "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("old", "model", "fc-old", "running", "2026-01-01T00:00:00+00:00", None, None),
            )
        manager = JobManager(legacy)
        self.assertEqual(manager.list()[0]["status"], "running")
        with closing(sqlite3.connect(legacy)) as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
            version = db.execute("PRAGMA user_version").fetchone()[0]
        self.assertTrue(
            {"updated_at", "error_code", "retryable", "artifact_remote_path", "remote_not_found_count"}.issubset(columns)
        )
        self.assertEqual(version, 3)

    def test_future_database_version_is_rejected(self) -> None:
        future = Path(self.temp.name) / "future.sqlite3"
        with closing(sqlite3.connect(future)) as db, db:
            db.execute("PRAGMA user_version = 99")
        with self.assertRaisesRegex(RuntimeError, "Job DB 版本过新"):
            JobManager(future)


if __name__ == "__main__":
    unittest.main()
