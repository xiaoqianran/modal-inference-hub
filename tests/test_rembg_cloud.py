from __future__ import annotations

import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from agent import rembg_preprocess


def _png_bytes(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


class ExecutionPreferenceTests(unittest.TestCase):
    def test_default_is_auto(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch(
            "agent.rembg_preprocess.data_dir", return_value=Path(temporary)
        ):
            self.assertEqual(rembg_preprocess.execution_preference(), "auto")

    def test_set_and_persist_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch(
            "agent.rembg_preprocess.data_dir", return_value=Path(temporary)
        ):
            rembg_preprocess.set_execution_preference("local")
            self.assertEqual(rembg_preprocess.execution_preference(), "local")
            rembg_preprocess.set_execution_preference("cloud")
            self.assertEqual(rembg_preprocess.execution_preference(), "cloud")
            rembg_preprocess.set_execution_preference("auto")
            self.assertEqual(rembg_preprocess.execution_preference(), "auto")

    def test_auto_resolves_to_local_when_gpu_available(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch(
            "agent.rembg_preprocess.data_dir", return_value=Path(temporary)
        ), patch(
            "agent.rembg_preprocess.available_providers", return_value=["cpu", "gpu"]
        ):
            self.assertEqual(rembg_preprocess.execution_preference(), "auto")
            self.assertEqual(rembg_preprocess.resolved_execution(), "local")

    def test_auto_resolves_to_cloud_without_gpu(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch(
            "agent.rembg_preprocess.data_dir", return_value=Path(temporary)
        ), patch(
            "agent.rembg_preprocess.available_providers", return_value=["cpu"]
        ):
            self.assertEqual(rembg_preprocess.resolved_execution(), "cloud")

    def test_explicit_choice_overrides_gpu_detection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch(
            "agent.rembg_preprocess.data_dir", return_value=Path(temporary)
        ), patch(
            "agent.rembg_preprocess.available_providers", return_value=["cpu", "gpu"]
        ):
            rembg_preprocess.set_execution_preference("cloud")
            self.assertEqual(rembg_preprocess.resolved_execution(), "cloud")
            rembg_preprocess.set_execution_preference("local")
            self.assertEqual(rembg_preprocess.resolved_execution(), "local")

    def test_invalid_execution_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "auto、cloud 或 local"):
            rembg_preprocess.set_execution_preference("hybrid")

    def test_resolved_execution_auto_prefers_local_gpu(self) -> None:
        with patch("agent.rembg_preprocess.execution_preference", return_value="auto"), patch(
            "agent.rembg_preprocess.available_providers", return_value=["cpu", "gpu"]
        ):
            self.assertEqual(rembg_preprocess.resolved_execution(), "local")

    def test_resolved_execution_auto_falls_back_to_cloud(self) -> None:
        with patch("agent.rembg_preprocess.execution_preference", return_value="auto"), patch(
            "agent.rembg_preprocess.available_providers", return_value=["cpu"]
        ):
            self.assertEqual(rembg_preprocess.resolved_execution(), "cloud")

    def test_resolved_execution_honours_explicit_choice(self) -> None:
        with patch("agent.rembg_preprocess.execution_preference", return_value="cloud"):
            self.assertEqual(rembg_preprocess.resolved_execution(), "cloud")


class CloudProcessTests(unittest.TestCase):
    def _payload(self, mask: Image.Image) -> dict:
        return {
            "mask_bytes_b64": base64.b64encode(_png_bytes(mask)).decode("ascii"),
            "source_size": [mask.width, mask.height],
            "engine": "birefnet-general-lite",
            "elapsed_ms": 12.3,
        }

    def _source(self) -> bytes:
        return _png_bytes(Image.new("RGB", (64, 64), (240, 240, 240)))

    def test_cloud_process_decodes_artifacts(self) -> None:
        # A full-foreground L mask so component analysis finds one object.
        mask = Image.new("L", (64, 64), 255)
        payload = self._payload(mask)

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(payload).encode("utf-8")

        with patch("agent.rembg_preprocess._cloud_url", return_value="http://cloud"), patch(
            "urllib.request.urlopen", return_value=Response()
        ):
            result = rembg_preprocess._cloud_process(self._source())

        self.assertEqual(result["provider"], "cloud")
        self.assertEqual(result["execution"], "cloud")
        self.assertEqual(result["source_size"], [64, 64])
        # canonical/component analysis is derived locally from the mask.
        self.assertEqual(result["component_count"], 1)
        self.assertEqual(result["selected_component_ids"], ["cc-00001"])
        canonical = Image.open(io.BytesIO(result["canonical_bytes"]))
        self.assertEqual(canonical.size, (1024, 1024))
        self.assertEqual(canonical.mode, "RGBA")

    def test_cloud_process_routes_when_not_connected(self) -> None:
        with patch("agent.modal_client.client", side_effect=rembg_preprocess.modal_client.NotConnectedError):
            with self.assertRaisesRegex(RuntimeError, "尚未连接"):
                rembg_preprocess._cloud_process(self._source())


if __name__ == "__main__":
    unittest.main()
