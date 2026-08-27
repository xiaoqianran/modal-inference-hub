from __future__ import annotations

import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent import modal_3d_deploy, modal_client


class Modal3DDeployTests(unittest.TestCase):
    def setUp(self) -> None:
        modal_3d_deploy._set_state(
            running=False,
            step=None,
            component=None,
            completed_apps=[],
            skipped_apps=[],
            component_errors={},
            component_states={},
            error=None,
        )

    def test_suite_contract_has_four_workers_and_gateway(self) -> None:
        self.assertEqual(len(modal_3d_deploy.WORKERS), 4)
        self.assertEqual(modal_3d_deploy.GATEWAY_APP, "modal-3d-gateway")
        self.assertEqual(
            [app for _module, app in modal_3d_deploy.WORKERS],
            [
                "modal-3d-fastsam3d",
                "modal-3d-hunyuan",
                "modal-3d-hermit-trellis2-plus-plus",
                "modal-3d-pixal3d",
            ],
        )

    def test_status_is_not_deployed_when_modal_is_disconnected(self) -> None:
        with patch.object(modal_client, "client", side_effect=modal_client.NotConnectedError), patch(
            "agent.modal_3d_deploy._source_ready", return_value=False
        ):
            status = modal_3d_deploy.status()
        self.assertFalse(status["deployed"])
        self.assertEqual(len(status["components"]), 5)
        self.assertTrue(all(not component["deployed"] for component in status["components"]))

    def test_function_exists_requires_remote_hydration(self) -> None:
        function = Mock()
        with patch("modal.Function.from_name", return_value=function):
            self.assertTrue(modal_3d_deploy._function_exists("app", "fn", object()))
        function.hydrate.assert_called_once()

    def test_function_exists_returns_false_on_lookup_error(self) -> None:
        function = Mock()
        function.hydrate.side_effect = RuntimeError("missing")
        with patch("modal.Function.from_name", return_value=function):
            self.assertFalse(modal_3d_deploy._function_exists("app", "fn", object()))

    def test_safe_extract_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as package:
                package.writestr("repo/../escape.py", "bad")
            with self.assertRaisesRegex(RuntimeError, "不安全路径"):
                modal_3d_deploy._safe_extract(archive, root / "out")

    def test_ensure_source_installs_verified_snapshot_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary) / "source"

            def fake_download(_url: str, destination: Path) -> None:
                with zipfile.ZipFile(destination, "w") as package:
                    package.writestr("repo/modal_3d/__init__.py", "")
                    package.writestr("repo/modal_3d/gateway.py", "APP_NAME='modal-3d-gateway'")

            with patch("agent.modal_3d_deploy._source_parent", return_value=parent), patch(
                "agent.modal_3d_deploy._download", side_effect=fake_download
            ) as download:
                first = modal_3d_deploy.ensure_source()
                second = modal_3d_deploy.ensure_source()

            self.assertEqual(first, second)
            self.assertTrue((first / "modal_3d" / "__init__.py").is_file())
            self.assertTrue((first / ".modal-3d-source.json").is_file())
            download.assert_called_once()

    def test_preflight_normalizes_missing_huggingface_secret(self) -> None:
        secret = Mock()
        secret.hydrate.side_effect = RuntimeError("not found")
        with patch("modal.Secret.from_name", return_value=secret):
            with self.assertRaisesRegex(RuntimeError, "huggingface"):
                modal_3d_deploy._preflight(object())

    def test_deploy_workers_register_then_gateway_and_verify(self) -> None:
        client = object()
        worker_modules = {}
        model_ids = ["fastsam3d-plus-plus", "hunyuan2.1-plus-plus", "hermit-trellis2-plus-plus", "pixal3d"]
        for (module_name, app_name), model_id in zip(modal_3d_deploy.WORKERS, model_ids, strict=True):
            app = Mock(name=f"app-{app_name}")
            worker_modules[module_name] = SimpleNamespace(app=app, CAPABILITY={"id": model_id})
        gateway_app = Mock(name="gateway-app")
        modules = {**worker_modules, "gateway": SimpleNamespace(app=gateway_app)}

        function_calls: list[tuple[str, str]] = []

        def function_from_name(app_name: str, function_name: str, **_kwargs):
            function_calls.append((app_name, function_name))
            function = Mock()
            if app_name == modal_3d_deploy.GATEWAY_APP and function_name == "capabilities":
                function.remote.return_value = {"models": [{"id": model_id} for model_id in model_ids]}
            else:
                function.remote.return_value = {"registered": app_name}
            return function

        with patch.object(modal_client, "client", return_value=client), patch(
            "agent.modal_3d_deploy._preflight"
        ), patch("agent.modal_3d_deploy.ensure_source"), patch(
            "agent.modal_3d_deploy._load_module", side_effect=lambda name: modules[name]
        ), patch("modal.Function.from_name", side_effect=function_from_name):
            result = modal_3d_deploy.deploy()

        for module_name, _app_name in modal_3d_deploy.WORKERS:
            worker_modules[module_name].app.deploy.assert_called_once_with(client=client)
        gateway_app.deploy.assert_called_once_with(client=client)
        self.assertEqual(
            set(function_calls[:-1]),
            {(app_name, "register") for _module, app_name in modal_3d_deploy.WORKERS},
        )
        self.assertEqual(function_calls[-1], (modal_3d_deploy.GATEWAY_APP, "capabilities"))
        self.assertTrue(result["ok"])
        self.assertTrue(result["deployed"])
        self.assertEqual(len(result["models"]), 4)

    def test_run_deploy_skips_already_deployed_components(self) -> None:
        client = object()
        modules = {}
        model_ids = ["fastsam3d-plus-plus", "hunyuan2.1-plus-plus", "hermit-trellis2-plus-plus", "pixal3d"]
        for (module_name, app_name), model_id in zip(modal_3d_deploy.WORKERS, model_ids, strict=True):
            modules[module_name] = SimpleNamespace(app=Mock(name=app_name), CAPABILITY={"id": model_id})
        modules["gateway"] = SimpleNamespace(app=Mock(name="gateway"))
        existing = {"modal-3d-fastsam3d", "modal-3d-hunyuan"}

        def from_name(app_name: str, function_name: str, **_kwargs):
            function = Mock()
            if app_name == modal_3d_deploy.GATEWAY_APP and function_name == "capabilities":
                function.remote.return_value = {"models": [{"id": model_id} for model_id in model_ids]}
            return function

        with patch("agent.modal_3d_deploy._preflight"), patch(
            "agent.modal_3d_deploy.ensure_source"
        ), patch(
            "agent.modal_3d_deploy._component_status",
            return_value=[{"app": app, "kind": "worker", "deployed": True} for app in existing],
        ), patch(
            "agent.modal_3d_deploy._load_module", side_effect=lambda name: modules[name]
        ), patch("modal.Function.from_name", side_effect=from_name):
            result = modal_3d_deploy._run_deploy(client)

        modules["fastsam3d_plus_plus"].app.deploy.assert_not_called()
        modules["hunyuan2_1_plus_plus"].app.deploy.assert_not_called()
        modules["gateway"].app.deploy.assert_called_once_with(client=client)
        modules["hermit_trellis2_plus_plus"].app.deploy.assert_called_once_with(client=client)
        modules["pixal3d"].app.deploy.assert_called_once_with(client=client)
        self.assertEqual(set(modal_3d_deploy.status()["skipped_apps"]), existing)
        self.assertTrue(result["deployed"])

    def test_component_failure_is_saved_for_status_polling(self) -> None:
        client = object()
        modules = {}
        for module_name, app_name in modal_3d_deploy.WORKERS:
            app = Mock(name=app_name)
            modules[module_name] = SimpleNamespace(app=app, CAPABILITY={"id": app_name})
        modules["fastsam3d_plus_plus"].app.deploy.side_effect = RuntimeError("image build failed")

        def from_name(app_name: str, _function_name: str, **_kwargs):
            function = Mock()
            function.remote.return_value = {"registered": app_name}
            return function

        with patch("agent.modal_3d_deploy._preflight"), patch(
            "agent.modal_3d_deploy.ensure_source"
        ), patch("agent.modal_3d_deploy._component_status", return_value=[]), patch(
            "agent.modal_3d_deploy._load_module", side_effect=lambda name: modules[name]
        ), patch("modal.Function.from_name", side_effect=from_name):
            with self.assertRaisesRegex(RuntimeError, "1 个 Worker 部署失败"):
                modal_3d_deploy._run_deploy(client)

        status = modal_3d_deploy.status()
        self.assertIn("image build failed", status["component_errors"]["modal-3d-fastsam3d"])
        self.assertEqual(status["component_states"]["modal-3d-fastsam3d"], "failed")
        self.assertEqual(status["component_states"][modal_3d_deploy.GATEWAY_APP], "blocked")
        self.assertEqual(len(status["completed_apps"]), 3)

    def test_workers_deploy_with_bounded_parallelism_before_gateway(self) -> None:
        client = object()
        state_lock = threading.Lock()
        release = threading.Event()
        active = 0
        maximum_active = 0
        modules = {}
        model_ids = []

        def deploy_worker(**_kwargs) -> None:
            nonlocal active, maximum_active
            with state_lock:
                active += 1
                maximum_active = max(maximum_active, active)
                if active == 3:
                    release.set()
            self.assertTrue(release.wait(timeout=2), "three workers did not overlap")
            with state_lock:
                active -= 1

        for module_name, app_name in modal_3d_deploy.WORKERS:
            app = Mock(name=app_name)
            app.deploy.side_effect = deploy_worker
            model_id = f"model-{module_name}"
            model_ids.append(model_id)
            modules[module_name] = SimpleNamespace(app=app, CAPABILITY={"id": model_id})
        modules["gateway"] = SimpleNamespace(app=Mock(name="gateway"))

        def from_name(app_name: str, function_name: str, **_kwargs):
            function = Mock()
            if app_name == modal_3d_deploy.GATEWAY_APP and function_name == "capabilities":
                function.remote.return_value = {"models": [{"id": model_id} for model_id in model_ids]}
            else:
                function.remote.return_value = {"registered": app_name}
            return function

        with patch("agent.modal_3d_deploy._preflight"), patch(
            "agent.modal_3d_deploy.ensure_source"
        ), patch("agent.modal_3d_deploy._component_status", return_value=[]), patch(
            "agent.modal_3d_deploy._load_module", side_effect=lambda name: modules[name]
        ), patch("modal.Function.from_name", side_effect=from_name), patch.dict(
            "os.environ", {"MODAL_3D_DEPLOY_CONCURRENCY": "3"}
        ):
            result = modal_3d_deploy._run_deploy(client)

        self.assertTrue(result["deployed"])
        self.assertEqual(maximum_active, 3)
        modules["gateway"].app.deploy.assert_called_once_with(client=client)
        status = modal_3d_deploy.status()
        self.assertTrue(all(value == "completed" for value in status["component_states"].values()))

    def test_start_deploy_returns_without_waiting_for_cloud_build(self) -> None:
        client = object()
        thread = Mock()
        with patch.object(modal_client, "client", return_value=client), patch(
            "agent.modal_3d_deploy.threading.Thread", return_value=thread
        ):
            result = modal_3d_deploy.start_deploy()
        try:
            self.assertTrue(result["accepted"])
            self.assertTrue(result["running"])
            thread.start.assert_called_once()
            status = modal_3d_deploy.status()
            self.assertTrue(status["running"])
            self.assertEqual(status["step"], "queued")
        finally:
            if modal_3d_deploy._lock.locked():
                modal_3d_deploy._lock.release()

    def test_start_deploy_is_idempotent_while_background_job_runs(self) -> None:
        modal_3d_deploy._lock.acquire()
        try:
            result = modal_3d_deploy.start_deploy()
            self.assertFalse(result["accepted"])
            self.assertTrue(result["running"])
        finally:
            modal_3d_deploy._lock.release()

    def test_deploy_rejects_parallel_run(self) -> None:
        modal_3d_deploy._lock.acquire()
        try:
            with self.assertRaisesRegex(RuntimeError, "正在部署"):
                modal_3d_deploy.deploy()
        finally:
            modal_3d_deploy._lock.release()


if __name__ == "__main__":
    unittest.main()
