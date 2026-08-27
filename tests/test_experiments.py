from __future__ import annotations

from hub.experiments import (
    ExperimentConflict,
    new_experiment,
    phase,
    plan_asset3d,
    record_candidate,
    select_candidate,
)


def experiment():
    return new_experiment(
        experiment_id="exp_test",
        prompt="brass telescope",
        candidate_count=2,
        image_model="image-model",
        seed=10,
        timestamp="2026-01-01T00:00:00+00:00",
    )


def test_pure_state_machine_requires_a_verified_human_selection() -> None:
    value = experiment()
    assert phase(value) == "generating-images"
    value = record_candidate(
        value,
        "candidate-1",
        {
            "status": "succeeded",
            "result": {"artifact": {"id": "image-1", "digest": "sha256:a"}},
        },
        "t1",
    )
    assert phase(value) == "generating-images"
    selected = select_candidate(value, "candidate-1", "t2")
    assert phase(selected) == "image-selected"
    planned = plan_asset3d(
        selected, model="mesh-model", profile="recommended", seed=42, timestamp="t3"
    )
    assert planned["asset3d"]["job"]["id"] == "hub3d_exp_test"
    assert value["selection"] is None  # every transition returns a fresh value


def test_selection_rejects_an_unfinished_candidate() -> None:
    try:
        select_candidate(experiment(), "candidate-1", "t1")
    except ExperimentConflict as exc:
        assert "succeeded" in str(exc)
    else:
        raise AssertionError("selection should fail")


def test_succeeded_execution_without_artifact_fails_closed() -> None:
    value = record_candidate(experiment(), "candidate-1", {"status": "succeeded"}, "t1")
    candidate = value["image"]["candidates"][0]
    assert candidate["job"]["state"] == "failed"
    assert candidate["failure"] == "provider.missing_artifact"
