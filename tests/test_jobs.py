from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from modal.exception import (
    AuthError,
    ConnectionError,
    FunctionTimeoutError,
    OutputExpiredError,
    RemoteError,
)

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

        value, _ = self.poll_with(job["id"], result={"artifact": {"path": "x.glb"}})
        self.assertEqual(value["status"], "succeeded")
        self.assertIsNotNone(value["result"])

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
        with sqlite3.connect(legacy) as db:
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
        with sqlite3.connect(legacy) as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
            version = db.execute("PRAGMA user_version").fetchone()[0]
        self.assertTrue({"updated_at", "error_code", "retryable"}.issubset(columns))
        self.assertEqual(version, 1)

    def test_future_database_version_is_rejected(self) -> None:
        future = Path(self.temp.name) / "future.sqlite3"
        with sqlite3.connect(future) as db:
            db.execute("PRAGMA user_version = 99")
        with self.assertRaisesRegex(RuntimeError, "Job DB 版本过新"):
            JobManager(future)


if __name__ == "__main__":
    unittest.main()
