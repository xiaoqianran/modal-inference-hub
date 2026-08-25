from __future__ import annotations

import io
import unittest
from unittest.mock import patch

from PIL import Image

from agent import rembg_preprocess


class RembgPreprocessTests(unittest.TestCase):
    def _source(self, size=(800, 400)) -> bytes:
        image = Image.new("RGB", size, (240, 240, 240))
        output = io.BytesIO()
        image.save(output, "PNG")
        return output.getvalue()

    def test_global_matte_is_letterboxed_without_aspect_distortion(self) -> None:
        mask = Image.new("L", (800, 400), 0)
        # 2:1 foreground should become 1024x512, vertically centred.
        mask.paste(255, (100, 100, 700, 400))
        with patch("agent.rembg_preprocess._predict_mask", return_value=mask):
            result = rembg_preprocess.process(self._source())
        canonical = Image.open(io.BytesIO(result["canonical_bytes"]))
        self.assertEqual(canonical.mode, "RGBA")
        self.assertEqual(canonical.size, (1024, 1024))
        alpha = canonical.getchannel("A")
        self.assertEqual(alpha.getbbox(), (0, 256, 1024, 768))
        self.assertEqual(result["foreground_bbox"], [100, 100, 700, 400])
        self.assertEqual(result["engine"], "birefnet-general")
        self.assertEqual(result["provider"], "cpu")

    def test_no_foreground_is_rejected(self) -> None:
        mask = Image.new("L", (800, 400), 0)
        with (
            patch("agent.rembg_preprocess._predict_mask", return_value=mask),
            self.assertRaisesRegex(ValueError, "未检测到可用前景"),
        ):
            rembg_preprocess.process(self._source())


if __name__ == "__main__":
    unittest.main()
