from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from agent import generation
from agent.models import IncompatibleCapability


class GatewaySubmissionTests(unittest.TestCase):
    def submit(self, response):
        fn = Mock()
        fn.remote.return_value = response
        with (
            patch("agent.generation.client", return_value=Mock()),
            patch("agent.generation.options_for", return_value={"seed": 42}),
            patch("agent.generation.modal.Function.from_name", return_value=fn),
        ):
            return generation.submit("pixal3d", "sam31/input.png")

    def test_current_gateway_submission_is_accepted(self) -> None:
        response = {
            "task_id": "fc-current",
            "call_id": "fc-current",
            "model": "pixal3d",
            "kind": "generation",
            "status": "running",
            "submitted_at": 1.0,
        }
        self.assertEqual(self.submit(response), response)

    def test_legacy_gateway_response_is_rejected(self) -> None:
        with self.assertRaisesRegex(IncompatibleCapability, "invalid task id"):
            self.submit({"model": "pixal3d", "status": "running", "call_id": "fc-legacy"})

    def test_mismatched_model_is_rejected(self) -> None:
        with self.assertRaisesRegex(IncompatibleCapability, "submission state"):
            self.submit(
                {
                    "task_id": "fc-wrong",
                    "call_id": "fc-wrong",
                    "model": "other",
                    "status": "running",
                }
            )


if __name__ == "__main__":
    unittest.main()
