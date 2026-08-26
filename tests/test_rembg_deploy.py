from __future__ import annotations

import unittest
from unittest.mock import patch

from agent import modal_client, rembg_deploy


class RembgDeployTests(unittest.TestCase):
    def test_module_app_matches_client_contract(self) -> None:
        self.assertEqual(rembg_deploy.app.name, "modal-3d-rembg")

    def test_deployed_false_when_not_connected(self) -> None:
        with patch.object(
            modal_client, "client", side_effect=modal_client.NotConnectedError
        ):
            self.assertFalse(rembg_deploy.deployed())

    def test_deployed_true_when_function_resolves(self) -> None:
        with patch.object(modal_client, "client", return_value=object()), patch(
            "modal.Function.from_name", return_value=object()
        ):
            self.assertTrue(rembg_deploy.deployed())

    def test_deployed_false_when_lookup_fails(self) -> None:
        with patch.object(modal_client, "client", return_value=object()), patch(
            "modal.Function.from_name", side_effect=Exception("not found")
        ):
            self.assertFalse(rembg_deploy.deployed())

    def test_deploy_requires_connection(self) -> None:
        with patch.object(
            modal_client,
            "require_client",
            side_effect=RuntimeError("Modal is not connected"),
        ):
            with self.assertRaisesRegex(RuntimeError, "not connected"):
                rembg_deploy.deploy()

    def test_deploy_returns_web_url_and_redeploy_flag(self) -> None:
        function = unittest.mock.Mock()
        function.get_web_url.return_value = "https://example.modal.run"

        with patch.object(modal_client, "require_client", return_value=object()), patch(
            "agent.rembg_deploy.app"
        ) as deployed_app, patch("agent.rembg_deploy.deployed", return_value=False), patch(
            "modal.Function.from_name", return_value=function
        ):
            result = rembg_deploy.deploy()

        deployed_app.deploy.assert_called_once()
        self.assertTrue(result["ok"])
        self.assertEqual(result["app"], "modal-3d-rembg")
        self.assertFalse(result["redeploy"])
        self.assertEqual(result["web_url"], "https://example.modal.run")


if __name__ == "__main__":
    unittest.main()
