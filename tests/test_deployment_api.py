from pathlib import Path

from hub.app import create_app
from hub.deployments import DeploymentService, DeploymentStore
from hub.experiments import ExperimentService, ExperimentStore
from tests.test_workflow_api import FakeSidecar, request


class FakeDeployer:
    def plan(self):
        return {"provider": "modal-2d", "apps": ["modal-2d"], "steps": []}

    def apply(self, token_id, token_secret, on_event):
        assert (token_id, token_secret) == ("id", "secret")
        on_event({"stage": "complete", "state": "succeeded", "result": {"ok": True}})


def test_deployment_api_exposes_provider_plan_and_durable_projection(tmp_path: Path):
    workflow = ExperimentService(
        ExperimentStore(tmp_path / "experiments.sqlite3"),
        FakeSidecar("modal-2d"),  # type: ignore[arg-type]
        FakeSidecar("modal-3d"),  # type: ignore[arg-type]
    )
    deployment = DeploymentService(
        DeploymentStore(tmp_path / "deployments.sqlite3"),
        {"modal-2d": FakeDeployer()},
        lambda task: task(),
    )
    app = create_app(workflow, deployment)
    assert request(app, "GET", "/api/providers/modal-2d/deployment-plan").json()["apps"] == [
        "modal-2d"
    ]
    started = request(
        app,
        "POST",
        "/api/providers/modal-2d/deployments",
        json={"token_id": "id", "token_secret": "secret"},
    )
    assert started.status_code == 202
    completed = request(app, "GET", f"/api/deployments/{started.json()['id']}").json()
    assert completed["state"] == "succeeded"
