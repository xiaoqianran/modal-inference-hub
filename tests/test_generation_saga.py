from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agent import main
from agent.generation_service import (
    GenerationCoordinator,
    GenerationRecoveryPending,
)
from agent.generation_store import GenerationConflict, GenerationSubmissionUnknown


class GenerationSagaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.projects = Mock()
        self.intents = Mock()
        self.jobs = Mock()
        self.coordinator = GenerationCoordinator(self.projects, self.intents, self.jobs)
        self.descriptor = {"id": "canonical-1", "sha256": "abc", "bytes": 123}
        self.projects.canonical_local.return_value = (self.descriptor, Path("canonical.png"))
        self.projects.canonical_remote.return_value = (self.descriptor, "remote/canonical.png")
        self.remote_probe = patch(
            "agent.generation_service.artifacts.describe_remote_png",
            return_value={"sha256": "abc", "bytes": 123, "path": "remote/canonical.png"},
        ).start()
        self.addCleanup(patch.stopall)
        self.projects.get.return_value = {"id": "project-1", "status": "generating"}
        self.intents.claim.return_value = {"claimed": True, "job_id": None, "state": "preparing"}
        self.intents.mark_remote.return_value = {
            "request_id": "request-generation-test",
            "project_id": "project-1",
            "model": "pixal3d",
            "remote_call_id": "fc-123",
        }
        self.jobs.create.return_value = {"id": "job-123", "status": "running"}

    def submit(self) -> dict:
        return self.coordinator.submit(
            "project-1", "request-generation-test", "pixal3d", "recommended", 42
        )

    @patch("agent.generation_service.generation.prepare_options", return_value={"seed": 42})
    @patch("agent.generation_service.generation.submit")
    def test_duplicate_generation_is_rejected_before_remote_submit(self, submit, _prepare) -> None:
        self.intents.claim.side_effect = GenerationConflict("该项目已有远程生成任务正在活动")

        with self.assertRaisesRegex(GenerationConflict, "已有远程生成任务"):
            self.submit()

        submit.assert_not_called()
        self.projects.canonical_local.assert_not_called()

    @patch("agent.generation_service.generation.prepare_options", return_value={"seed": 42})
    @patch("agent.generation_service.generation.submit")
    def test_same_request_returns_bound_job_without_remote_submit(self, submit, _prepare) -> None:
        self.intents.claim.return_value = {
            "claimed": False,
            "job_id": "job-existing",
            "state": "bound",
        }
        self.jobs.get.return_value = {"id": "job-existing", "status": "running"}

        result = self.submit()

        self.assertEqual(result["job"]["id"], "job-existing")
        submit.assert_not_called()

    @patch("agent.generation_service.generation.prepare_options", return_value={"seed": 42})
    @patch("agent.generation_service.generation.submit")
    @patch("agent.generation_service.artifacts.put")
    def test_changed_canonical_is_reuploaded_before_remote_submit(
        self, put, submit, _prepare
    ) -> None:
        local_path = Mock()
        local_path.read_bytes.return_value = b"canonical-bytes"
        self.projects.canonical_local.return_value = (self.descriptor, local_path)
        self.projects.canonical_remote.side_effect = RuntimeError("stale canonical")
        put.return_value = {"path": "remote/new.png", "sha256": "abc", "bytes": 123}
        submit.return_value = {"model": "pixal3d", "call_id": "fc-123"}

        self.submit()

        put.assert_called_once_with(b"canonical-bytes", ".png")
        self.projects.record_remote_canonical.assert_called_once_with(
            "project-1", "remote/new.png", "abc"
        )
        self.intents.begin_remote.assert_called_once_with("request-generation-test")
        submit.assert_called_once_with(
            "pixal3d", "remote/new.png", "recommended", 42, options={"seed": 42}
        )
        self.intents.mark_remote.assert_called_once_with("request-generation-test", "fc-123")
        self.intents.bind_job.assert_called_once_with("request-generation-test", "job-123")

    @patch("agent.generation_service.generation.prepare_options", return_value={"seed": 42})
    @patch("agent.generation_service.generation.submit")
    @patch("agent.generation_service.artifacts.put")
    def test_missing_remote_canonical_is_reuploaded_before_paid_submit(
        self, put, submit, _prepare
    ) -> None:
        local_path = Mock()
        local_path.read_bytes.return_value = b"canonical-bytes"
        self.projects.canonical_local.return_value = (self.descriptor, local_path)
        self.remote_probe.side_effect = FileNotFoundError("remote canonical expired")
        put.return_value = {"path": "remote/recovered.png", "sha256": "abc", "bytes": 123}
        submit.return_value = {"model": "pixal3d", "call_id": "fc-123"}

        self.submit()

        self.remote_probe.assert_called_once_with("remote/canonical.png", expected_bytes=123)
        put.assert_called_once_with(b"canonical-bytes", ".png")
        self.projects.record_remote_canonical.assert_called_once_with(
            "project-1", "remote/recovered.png", "abc"
        )
        submit.assert_called_once_with(
            "pixal3d", "remote/recovered.png", "recommended", 42, options={"seed": 42}
        )

    @patch("agent.generation_service.generation.prepare_options", return_value={"seed": 42})
    @patch("agent.generation_service.generation.submit")
    @patch("agent.generation_service.artifacts.put")
    def test_remote_probe_network_error_does_not_reupload_or_submit(
        self, put, submit, _prepare
    ) -> None:
        self.remote_probe.side_effect = ConnectionError("network unavailable")

        with self.assertRaises(ConnectionError):
            self.submit()

        put.assert_not_called()
        submit.assert_not_called()
        self.intents.release_pre_remote.assert_called_once_with("request-generation-test")
        self.intents.begin_remote.assert_not_called()

    @patch("agent.generation_service.generation.prepare_options", return_value={"seed": 42})
    @patch("agent.generation_service.generation.submit", side_effect=TimeoutError("lost response"))
    def test_submit_transport_error_fails_closed(self, _submit, _prepare) -> None:
        with self.assertRaises(GenerationSubmissionUnknown):
            self.submit()

        self.intents.begin_remote.assert_called_once_with("request-generation-test")
        self.intents.mark_uncertain.assert_called_once()
        self.intents.release_pre_remote.assert_not_called()
        self.jobs.create.assert_not_called()

    @patch("agent.generation_service.generation.prepare_options", return_value={"seed": 42})
    @patch("agent.generation_service.generation.submit")
    def test_pre_remote_failure_releases_intent(self, submit, _prepare) -> None:
        self.projects.canonical_local.side_effect = FileNotFoundError("canonical missing")

        with self.assertRaises(FileNotFoundError):
            self.submit()

        self.intents.release_pre_remote.assert_called_once_with("request-generation-test")
        self.intents.begin_remote.assert_not_called()
        submit.assert_not_called()

    @patch("agent.generation_service.generation.prepare_options", return_value={"seed": 42})
    @patch("agent.generation_service.generation.cancel_call")
    @patch("agent.generation_service.generation.submit")
    def test_remote_id_persistence_failure_blocks_retry(
        self, submit, cancel_call, _prepare
    ) -> None:
        submit.return_value = {"model": "pixal3d", "call_id": "fc-123"}
        self.intents.mark_remote.side_effect = RuntimeError("db unavailable")

        with self.assertRaises(GenerationSubmissionUnknown):
            self.submit()

        cancel_call.assert_called_once_with("fc-123")
        self.intents.mark_uncertain.assert_called_once()
        self.intents.release_pre_remote.assert_not_called()

    @patch("agent.generation_service.generation.prepare_options", return_value={"seed": 42})
    @patch("agent.generation_service.generation.cancel_call")
    @patch("agent.generation_service.generation.submit")
    def test_local_bind_failure_keeps_remote_for_recovery(
        self, submit, cancel_call, _prepare
    ) -> None:
        submit.return_value = {"model": "pixal3d", "call_id": "fc-123"}
        self.jobs.create.side_effect = RuntimeError("job db unavailable")

        with self.assertRaises(GenerationRecoveryPending):
            self.submit()

        self.intents.mark_remote.assert_called_once_with("request-generation-test", "fc-123")
        cancel_call.assert_not_called()
        self.intents.mark_uncertain.assert_not_called()

    @patch("agent.generation_service.generation.prepare_options", return_value={"seed": 42})
    @patch("agent.generation_service.generation.submit")
    def test_remote_created_replay_resumes_without_resubmit(self, submit, _prepare) -> None:
        self.intents.claim.return_value = {
            "claimed": False,
            "job_id": None,
            "state": "remote_created",
        }
        self.intents.get.return_value = self.intents.mark_remote.return_value

        result = self.submit()

        self.assertEqual(result["job"]["id"], "job-123")
        submit.assert_not_called()
        self.intents.bind_job.assert_called_once_with("request-generation-test", "job-123")


class GenerationRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.request = main.ProjectGenerationRequest(
            request_id="request-generation-test",
            model="pixal3d",
            profile="recommended",
            seed=42,
        )

    def test_route_delegates_to_coordinator(self) -> None:
        expected = {"project": {"id": "project-1"}, "job": {"id": "job-1"}}
        with patch.object(main.generation_coordinator, "submit", return_value=expected) as submit:
            result = main.project_generation("project-1", self.request)

        self.assertEqual(result, expected)
        submit.assert_called_once_with(
            "project-1", "request-generation-test", "pixal3d", "recommended", 42
        )

    def test_uncertain_submission_maps_to_conflict(self) -> None:
        with patch.object(
            main.generation_coordinator,
            "submit",
            side_effect=GenerationSubmissionUnknown("unknown"),
        ):
            with self.assertRaises(main.HTTPException) as raised:
                main.project_generation("project-1", self.request)

        self.assertEqual(raised.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
