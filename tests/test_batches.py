from pathlib import Path

from test_workflow_api import PNG, FakeSidecar

from hub.batches import BatchService, BatchStore, normalize_prompts
from hub.direct_images import DirectImageService, DirectImageStore, InputStore
from hub.experiments import ExperimentService, ExperimentStore


def immediate(task):
    task()


def services(tmp_path: Path):
    image = FakeSidecar("modal-2d")
    asset3d = FakeSidecar("modal-3d")
    experiments = ExperimentService(
        ExperimentStore(tmp_path / "experiments.sqlite3"),
        image,  # type: ignore[arg-type]
        asset3d,  # type: ignore[arg-type]
    )
    direct = DirectImageService(
        DirectImageStore(tmp_path / "direct.sqlite3"),
        InputStore(tmp_path / "inputs"),
        asset3d,  # type: ignore[arg-type]
    )
    batch = BatchService(
        BatchStore(tmp_path / "batches.sqlite3"), experiments, direct, immediate
    )
    return batch, direct


def test_prompt_batch_references_experiments_and_stops_for_human_review(tmp_path: Path):
    batch, _ = services(tmp_path)
    created = batch.create_prompts(
        {
            "prompts": [" brass telescope ", "ceramic robot"],
            "candidate_count": 1,
            "image_model": "modal-2d-model",
            "seed": 7,
        }
    )
    value = batch.get(created["id"])
    assert value["state"] == "awaiting_review"
    assert value["summary"]["awaiting_review"] == 2
    assert all(item["target"]["kind"] == "experiment" for item in value["items"])
    assert "image" not in value["items"][0]


def test_image_batch_reuses_direct_image_slice_with_bounded_dispatch(tmp_path: Path):
    batch, direct = services(tmp_path)
    sources = [direct.ingest(PNG, f"asset-{index}.png") for index in range(2)]
    created = batch.create_images(
        sources, model="modal-3d-model", profile="recommended", seed=42
    )
    value = batch.get(created["id"])
    assert value["state"] == "succeeded"
    assert value["summary"]["succeeded"] == 1
    assert value["summary"]["total"] == 1  # identical content is not submitted twice
    assert all(item["target"]["kind"] == "direct-image" for item in value["items"])


def test_prompt_normalization_drops_only_empty_lines():
    assert normalize_prompts([" one ", "", "two", "one"]) == ["one", "two"]
