from pathlib import Path

import pytest
from test_workflow_api import GLB, PNG, FakeSidecar

from hub.direct_images import (
    DirectImageError,
    DirectImageService,
    DirectImageStore,
    InputStore,
    describe_input,
)


def test_input_descriptor_rejects_unknown_and_hashes_supported_images():
    with pytest.raises(DirectImageError):
        describe_input(b"not an image", "bad.bin")
    value = describe_input(PNG, "../asset.png")
    assert value["mediaType"] == "image/png"
    assert value["name"] == "asset.png"
    assert len(value["sha256"]) == 64


def test_direct_image_run_is_durable_and_uses_stable_job_id(tmp_path: Path):
    sidecar = FakeSidecar("modal-3d")
    service = DirectImageService(
        DirectImageStore(tmp_path / "runs.sqlite3"),
        InputStore(tmp_path / "inputs"),
        sidecar,  # type: ignore[arg-type]
    )
    source = service.ingest(PNG, "input.png")
    started = service.create(
        source,
        model="modal-3d-model",
        profile="recommended",
        seed=42,
        run_id="img3d_test",
    )
    assert started["job"]["id"] == "hub3d_img3d_test"
    completed = service.get("img3d_test")
    assert completed["job"]["state"] == "succeeded"
    stream, _ = service.artifact("img3d_test")
    assert b"".join(stream) == GLB
