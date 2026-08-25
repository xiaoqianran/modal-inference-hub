from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agent import main


class GenerationSagaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.request = main.ProjectGenerationRequest(
            model="pixal3d", profile="recommended", seed=42
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
        store.get.return_value = {"id": "project-1"}
        store.canonical_local.return_value = (self.descriptor, Path("canonical.png"))
        store.canonical_remote.return_value = (self.descriptor, "remote/canonical.png")
        return store

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


if __name__ == "__main__":
    unittest.main()
