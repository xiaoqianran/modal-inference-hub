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
    def test_default_is_cloud(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch(
            "agent.storage.data_dir", return_value=Path(temporary)
        ):
            self.assertEqual(rembg_preprocess.execution_preference(), "cloud")

    def test_set_and_persist_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch(
            "agent.storage.data_dir", return_value=Path(temporary)
        ):
            rembg_preprocess.set_execution_preference("local")
            self.assertEqual(rembg_preprocess.execution_preference(), "local")
            rembg_preprocess.set_execution_preference("cloud")
            self.assertEqual(rembg_preprocess.execution_preference(), "cloud")

    def test_invalid_execution_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "cloud 或 local"):
            rembg_preprocess.set_execution_preference("hybrid")


class CloudProcessTests(unittest.TestCase):
    def _payload(self, rgba: Image.Image) -> dict:
        return {
            "matte_bytes_b64": base64.b64encode(_png_bytes(rgba)).decode("ascii"),
            "source_size": [rgba.width, rgba.height],
            "engine": "birefnet-general-lite",
            "elapsed_ms": 12.3,
        }

    def _source(self) -> bytes:
        return _png_bytes(Image.new("RGB", (64, 64), (240, 240, 240)))

    def test_cloud_process_decodes_artifacts(self) -> None:
        rgba = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        rgba.paste((220, 40, 40, 255), (0, 0, 64, 64))
        payload = self._payload(rgba)

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
        # canonical/component analysis is derived locally from the matte.
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
