from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agent import main


class GenerationSagaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.request = main.ProjectGenerationRequest(
            request_id="request-generation-test",
            model="pixal3d",
            profile="recommended",
            seed=42,
        )
        self.descriptor = {
            "id": "canonical-1",
            "sha256": "abc",
            "bytes": 123,
        }
        self.remote = {
            "model": "pixal3d",
            "call_id": "fc-123",
        }

    def _project_store(self) -> Mock:
        store = Mock()
        store.claim_generation.return_value = {
            "claimed": True,
            "project": {"id": "project-1", "status": "submitting"},
            "job_id": None,
        }
        store.canonical_local.return_value = (self.descriptor, Path("canonical.png"))
        store.canonical_remote.return_value = (self.descriptor, "remote/canonical.png")
        return store

    def test_duplicate_generation_is_rejected_before_remote_submit(self) -> None:
        project_store = self._project_store()
        project_store.claim_generation.side_effect = ValueError(
            "该项目已有远程生成任务正在活动"
        )

        with (
            patch.object(main, "projects", project_store),
            patch.object(main.generation, "submit") as submit,
        ):
            with self.assertRaisesRegex(main.HTTPException, "已有远程生成任务") as raised:
                main.project_generation("project-1", self.request)

        self.assertEqual(raised.exception.status_code, 409)
        submit.assert_not_called()
        project_store.canonical_local.assert_not_called()

    def test_same_request_returns_bound_job_without_remote_submit(self) -> None:
        project_store = self._project_store()
        project_store.claim_generation.return_value = {
            "claimed": False,
            "project": {"id": "project-1", "status": "generating"},
            "job_id": "job-existing",
        }
        job_manager = Mock()
        job_manager.get.return_value = {"id": "job-existing", "status": "running"}

        with (
            patch.object(main, "projects", project_store),
            patch.object(main, "jobs", job_manager),
            patch.object(main.generation, "submit") as submit,
        ):
            result = main.project_generation("project-1", self.request)

        self.assertEqual(result["job"]["id"], "job-existing")
        job_manager.get.assert_called_once_with("job-existing")
        submit.assert_not_called()

    def test_changed_canonical_is_reuploaded_before_generation(self) -> None:
        project_store = self._project_store()
        project_store.canonical_remote.side_effect = RuntimeError("stale canonical")
        local_path = Mock()
        local_path.read_bytes.return_value = b"canonical-bytes"
        project_store.canonical_local.return_value = (self.descriptor, local_path)
        job_manager = Mock()
        job_manager.create.return_value = {"id": "job-123", "status": "running"}
        project_store.record_generation.return_value = {"id": "project-1", "status": "generating"}
        uploaded = {"path": "remote/new.png", "sha256": "abc", "bytes": 123}

        with (
            patch.object(main, "projects", project_store),
            patch.object(main, "jobs", job_manager),
            patch.object(main.artifacts, "put", return_value=uploaded) as put,
            patch.object(main.generation, "submit", return_value=self.remote) as submit,
        ):
            main.project_generation("project-1", self.request)

        put.assert_called_once_with(b"canonical-bytes", ".png")
        project_store.record_remote_canonical.assert_called_once_with(
            "project-1", "remote/new.png", "abc"
        )
        submit.assert_called_once_with("pixal3d", "remote/new.png", "recommended", 42)
        project_store.record_generation.assert_called_once_with(
            "project-1",
            "pixal3d",
            "recommended",
            "job-123",
            "request-generation-test",
        )
        project_store.release_generation_claim.assert_not_called()

    def test_job_persistence_failure_cancels_unbound_remote_call(self) -> None:
        project_store = self._project_store()
        job_manager = Mock()
        job_manager.create.side_effect = RuntimeError("job db unavailable")

        with (
            patch.object(main, "projects", project_store),
            patch.object(main, "jobs", job_manager),
            patch.object(main.generation, "submit", return_value=self.remote),
            patch.object(main.generation, "cancel_call") as cancel_call,
        ):
            with self.assertRaisesRegex(RuntimeError, "job db unavailable"):
                main.project_generation("project-1", self.request)

        cancel_call.assert_called_once_with("fc-123")
        project_store.record_generation.assert_not_called()
        project_store.release_generation_claim.assert_called_once_with(
            "project-1", "request-generation-test"
        )

    def test_project_binding_failure_persists_cancel_intent_on_job(self) -> None:
        project_store = self._project_store()
        project_store.record_generation.side_effect = RuntimeError("project db unavailable")
        job_manager = Mock()
        job_manager.create.return_value = {"id": "job-123", "status": "running"}

        with (
            patch.object(main, "projects", project_store),
            patch.object(main, "jobs", job_manager),
            patch.object(main.generation, "submit", return_value=self.remote),
        ):
            with self.assertRaisesRegex(RuntimeError, "project db unavailable"):
                main.project_generation("project-1", self.request)

        job_manager.cancel.assert_called_once_with("job-123")
        project_store.release_generation_claim.assert_called_once_with(
            "project-1", "request-generation-test"
        )


if __name__ == "__main__":
    unittest.main()
