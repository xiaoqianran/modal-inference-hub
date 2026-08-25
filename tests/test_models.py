from __future__ import annotations

import json
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch

from agent import models


def capability_fixture() -> dict:
    return {
        "contract": models.CONTRACT,
        "generation": {
            "app": models.APP_NAME,
            "submit_function": "submit",
            "job_transport": "modal.FunctionCall",
            "input_contract": {
                "role": "canonical_rgba",
                "mime": "image/png",
                "mode": "RGBA",
                "width": 1024,
                "height": 1024,
                "bit_depth": 8,
                "layout": "letterbox",
                "alpha": "channel_required",
            },
        },
        "models": [
            {
                "id": "fastsam3d-plus-plus",
                "name": "FastSAM3D++",
                "description": "fast",
                "status": "enabled",
                "worker_app": "modal-3d-fastsam3d",
                "output": "geometry",
                "artifact": {"mime": "model/gltf-binary", "extension": ".glb"},
                "input": {
                    "role": "canonical_rgba",
                    "mime": "image/png",
                    "mode": "RGBA",
                    "width": 1024,
                    "height": 1024,
                    "bit_depth": 8,
                    "layout": "letterbox",
                    "alpha": "channel_required",
                },
                "reference": {"warm_seconds": 6.06},
                "profiles": [
                    {
                        "id": "recommended",
                        "name": "推荐 · 已验证",
                        "options": {"dmd_interval": 1, "dmd_history": 5},
                    }
                ],
                "options": {
                    "seed": {"type": "integer", "default": 42},
                    "dmd_interval": {"type": "integer", "default": 1},
                    "dmd_history": {"type": "integer", "default": 5},
                },
            },
            {
                "id": "pixal3d",
                "name": "Pixal3D",
                "description": "textured",
                "status": "enabled",
                "worker_app": "modal-3d-pixal3d",
                "output": "textured",
                "artifact": {"mime": "model/gltf-binary", "extension": ".glb"},
                "input": {
                    "role": "canonical_rgba",
                    "mime": "image/png",
                    "mode": "RGBA",
                    "width": 1024,
                    "height": 1024,
                    "bit_depth": 8,
                    "layout": "letterbox",
                    "alpha": "channel_required",
                },
                "reference": {"warm_seconds": 108.92},
                "profiles": [
                    {
                        "id": "recommended",
                        "name": "推荐 · 已验证",
                        "options": {"fov": None},
                    }
                ],
                "options": {
                    "seed": {"type": "integer", "default": 42},
                    "fov": {"type": "number", "default": None, "nullable": True},
                },
            },
        ],
    }


class ModelCapabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.old_data = os.environ.get("MODAL_3D_AGENT_DATA_DIR")
        os.environ["MODAL_3D_AGENT_DATA_DIR"] = self.temp.name

    def tearDown(self) -> None:
        if self.old_data is None:
            os.environ.pop("MODAL_3D_AGENT_DATA_DIR", None)
        else:
            os.environ["MODAL_3D_AGENT_DATA_DIR"] = self.old_data
        self.temp.cleanup()

    def test_remote_capability_is_validated_and_cached(self) -> None:
        fn = Mock()
        fn.remote.return_value = capability_fixture()
        with (
            patch("agent.models.client", return_value=Mock()),
            patch("agent.models.connected", return_value=True),
            patch("agent.models.modal.Function.from_name", return_value=fn),
        ):
            value = models.capabilities_document()
        self.assertEqual(value["contract"], models.CONTRACT)
        cached = json.loads((Path(self.temp.name) / "generation-capabilities.json").read_text())
        self.assertEqual(cached["models"][0]["id"], "fastsam3d-plus-plus")

    def test_disconnected_uses_last_known_good_cache(self) -> None:
        cached = capability_fixture()
        (Path(self.temp.name) / "generation-capabilities.json").write_text(json.dumps(cached))
        with patch("agent.models.connected", return_value=False):
            value = models.capabilities_document()
        self.assertEqual(value["models"][1]["id"], "pixal3d")

    def test_first_run_without_connection_or_cache_is_explicit(self) -> None:
        with (
            patch("agent.models.connected", return_value=False),
            self.assertRaisesRegex(models.CapabilityUnavailable, "请先连接 Modal"),
        ):
            models.capabilities_document()

    def test_incompatible_remote_contract_never_falls_back_to_cache(self) -> None:
        cached = capability_fixture()
        (Path(self.temp.name) / "generation-capabilities.json").write_text(json.dumps(cached))
        incompatible = capability_fixture()
        incompatible["contract"] = "modal-3d.capabilities.v1"
        fn = Mock()
        fn.remote.return_value = incompatible
        with (
            patch("agent.models.client", return_value=Mock()),
            patch("agent.models.connected", return_value=True),
            patch("agent.models.modal.Function.from_name", return_value=fn),
            self.assertRaisesRegex(models.IncompatibleCapability, "incompatible"),
        ):
            models.capabilities_document()

    def test_source_input_limits_are_local_not_cloud_sam(self) -> None:
        self.assertEqual(models.source_input_limits()["max_bytes"], 20 * 1024 * 1024)
        self.assertEqual(models.source_input_limits()["max_pixels"], 40_000_000)
        self.assertEqual(models.source_input_limits()["mime"], ["image/png", "image/jpeg", "image/webp"])

    def test_public_models_are_projection_not_local_facts(self) -> None:
        with patch("agent.models.capabilities_document", return_value=capability_fixture()):
            public = models.public_models()
        self.assertEqual(
            public,
            [
                {
                    "id": "fastsam3d-plus-plus",
                    "name": "FastSAM3D++",
                    "description": "fast",
                    "status": "enabled",
                    "output": "geometry",
                    "warm_seconds": 6.06,
                    "profiles": [{"id": "recommended", "name": "推荐 · 已验证"}],
                },
                {
                    "id": "pixal3d",
                    "name": "Pixal3D",
                    "description": "textured",
                    "status": "enabled",
                    "output": "textured",
                    "warm_seconds": 108.92,
                    "profiles": [{"id": "recommended", "name": "推荐 · 已验证"}],
                },
            ],
        )

    def test_new_model_id_projects_without_client_registry_change(self) -> None:
        document = capability_fixture()
        future = deepcopy(document["models"][1])
        future["id"] = "future-model"
        future["name"] = "Future Model"
        document["models"].append(future)
        with patch("agent.models.capabilities_document", return_value=document):
            public = models.public_models()
            options = models.options_for("future-model", "recommended", 9)
        self.assertEqual(public[-1]["id"], "future-model")
        self.assertEqual(options, {"seed": 9, "fov": None})

    def test_options_are_expanded_from_capability_profile(self) -> None:
        with patch("agent.models.capabilities_document", return_value=capability_fixture()):
            self.assertEqual(
                models.options_for("fastsam3d-plus-plus", "recommended", 7),
                {"seed": 7, "dmd_interval": 1, "dmd_history": 5},
            )
            self.assertEqual(
                models.options_for("pixal3d", "recommended", 42),
                {"seed": 42, "fov": None},
            )

    def test_disabled_model_cannot_submit(self) -> None:
        document = capability_fixture()
        document["models"][0]["status"] = "disabled"
        with (
            patch("agent.models.capabilities_document", return_value=document),
            self.assertRaisesRegex(ValueError, "当前不可用"),
        ):
            models.options_for("fastsam3d-plus-plus", "recommended", 42)

    def test_duplicate_model_and_profile_are_rejected(self) -> None:
        duplicate_model = capability_fixture()
        duplicate_model["models"].append(deepcopy(duplicate_model["models"][0]))
        with self.assertRaisesRegex(models.IncompatibleCapability, "duplicate model"):
            models._validate_document(duplicate_model)

        duplicate_profile = capability_fixture()
        duplicate_profile["models"][0]["profiles"].append(
            deepcopy(duplicate_profile["models"][0]["profiles"][0])
        )
        with self.assertRaisesRegex(models.IncompatibleCapability, "duplicate profile"):
            models._validate_document(duplicate_profile)


if __name__ == "__main__":
    unittest.main()
