from pathlib import Path

from test_batches import immediate
from test_workflow_api import PNG, FakeSidecar, request

from hub.app import create_app
from hub.batches import BatchService, BatchStore
from hub.direct_images import DirectImageService, DirectImageStore, InputStore
from hub.experiments import ExperimentService, ExperimentStore


def test_batch_api_accepts_prompt_lists_and_ingested_images(tmp_path: Path):
    image = FakeSidecar("modal-2d")
    asset3d = FakeSidecar("modal-3d")
    workflow = ExperimentService(
        ExperimentStore(tmp_path / "experiments.sqlite3"),
        image,  # type: ignore[arg-type]
        asset3d,  # type: ignore[arg-type]
    )
    direct = DirectImageService(
        DirectImageStore(tmp_path / "direct.sqlite3"),
        InputStore(tmp_path / "inputs"),
        asset3d,  # type: ignore[arg-type]
    )
    batches = BatchService(
        BatchStore(tmp_path / "batches.sqlite3"), workflow, direct, immediate
    )
    app = create_app(workflow, direct_image_service=direct, batch_service=batches)

    prompts = request(
        app,
        "POST",
        "/api/batches/prompts",
        json={"prompts": ["one", "two"], "candidate_count": 1, "image_model": "model"},
    )
    assert prompts.status_code == 202
    assert request(app, "GET", f"/api/batches/{prompts.json()['id']}").json()["state"] == (
        "awaiting_review"
    )

    source = request(
        app,
        "POST",
        "/api/inputs/images",
        content=PNG,
        headers={"Content-Type": "application/octet-stream", "X-File-Name": "asset.png"},
    )
    assert source.status_code == 201
    images = request(
        app,
        "POST",
        "/api/batches/images",
        json={"sources": [source.json()], "model": "modal-3d-model"},
    )
    assert images.status_code == 202
    assert request(app, "GET", f"/api/batches/{images.json()['id']}").json()["state"] == (
        "succeeded"
    )
