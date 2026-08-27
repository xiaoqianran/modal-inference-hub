from pathlib import Path

from hub.deployments import (
    DeploymentService,
    DeploymentStore,
    ProviderDeployer,
    record_event,
)


class FakeDeployer:
    def __init__(self) -> None:
        self.credentials = None

    def plan(self):
        return {"provider": "modal-2d", "apps": ["owned-by-provider"], "steps": []}

    def apply(self, token_id, token_secret, on_event):
        self.credentials = (token_id, token_secret)
        on_event({"stage": "provider.deploy", "state": "running", "message": "部署"})
        on_event(
            {
                "stage": "complete",
                "state": "succeeded",
                "message": "完成",
                "result": {"verified": True},
            }
        )


def immediate(task):
    task()


def test_event_reducer_keeps_a_small_public_projection():
    document = {
        "state": "queued",
        "stage": "queued",
        "events": [],
        "result": None,
        "error": None,
        "updatedAt": "before",
    }
    updated = record_event(
        document,
        {"stage": "complete", "state": "succeeded", "result": {"verified": True}},
        "after",
    )
    assert updated["state"] == "succeeded"
    assert updated["result"] == {"verified": True}
    assert document["state"] == "queued"


def test_service_does_not_persist_credentials(tmp_path: Path):
    deployer = FakeDeployer()
    store = DeploymentStore(tmp_path / "deployments.sqlite3")
    service = DeploymentService(store, {"modal-2d": deployer}, immediate)
    assert service.plan("modal-2d")["apps"] == ["owned-by-provider"]
    started = service.start("modal-2d", "ak-test", "as-test")
    completed = service.get(started["id"])
    assert completed["state"] == "succeeded"
    assert deployer.credentials == ("ak-test", "as-test")
    assert "ak-test" not in store.path.read_bytes().decode(errors="ignore")
    assert "as-test" not in store.path.read_bytes().decode(errors="ignore")


def test_provider_deployer_keeps_credentials_out_of_argv_and_redacts_events(
    tmp_path: Path, monkeypatch
):
    captured = {}

    class Process:
        stdout = iter(
            ['{"stage":"complete","state":"succeeded","message":"ak-test as-test"}\n']
        )

        @staticmethod
        def wait():
            return 0

    def popen(argv, **kwargs):
        captured.update({"argv": argv, **kwargs})
        return Process()

    monkeypatch.setattr("hub.deployments.shutil.which", lambda _name: "uv")
    monkeypatch.setattr("hub.deployments.subprocess.Popen", popen)
    events = []
    ProviderDeployer("modal-2d", tmp_path, "modal_2d").apply(
        "ak-test", "as-test", events.append
    )
    assert "ak-test" not in " ".join(captured["argv"])
    assert "as-test" not in " ".join(captured["argv"])
    assert captured["env"]["MODAL_TOKEN_ID"] == "ak-test"
    assert events[0]["message"] == "*** ***"
