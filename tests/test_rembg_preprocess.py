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


class ComponentSelectionTests(unittest.TestCase):
    @staticmethod
    def _matte() -> bytes:
        rgba = Image.new("RGBA", (400, 200), (0, 0, 0, 0))
        rgba.paste((220, 40, 40, 255), (20, 20, 180, 180))
        rgba.paste((40, 80, 220, 255), (260, 50, 380, 150))
        output = io.BytesIO()
        rgba.save(output, "PNG")
        return output.getvalue()

    def test_detects_disconnected_objects_and_sorts_by_area(self) -> None:
        analysis = rembg_preprocess.analyze_components(self._matte())
        self.assertEqual(analysis["component_count"], 2)
        self.assertEqual(analysis["raw_component_count"], 2)
        first, second = analysis["components"]
        self.assertGreater(first["area_pixels"], second["area_pixels"])
        self.assertEqual(first["bbox"], [20, 20, 180, 180])
        self.assertEqual(second["bbox"], [260, 50, 380, 150])

    def test_all_selected_preserves_full_matte_canonical(self) -> None:
        matte = self._matte()
        analysis = rembg_preprocess.analyze_components(matte)
        ids = [item["id"] for item in analysis["components"]]
        selected = rembg_preprocess.canonicalize_components(matte, ids)
        with Image.open(io.BytesIO(matte)) as rgba:
            expected = rembg_preprocess._letterbox_rgba(
                rgba.convert("RGBA"),
                rembg_preprocess._foreground_bbox(rgba.getchannel("A")),
            )
        self.assertEqual(selected["canonical_bytes"], rembg_preprocess._png_bytes(expected))

    def test_selecting_one_object_removes_the_other_and_reboxes(self) -> None:
        matte = self._matte()
        analysis = rembg_preprocess.analyze_components(matte)
        first = analysis["components"][0]
        result = rembg_preprocess.canonicalize_components(matte, [first["id"]])
        self.assertEqual(result["foreground_bbox"], first["bbox"])
        self.assertEqual(result["selected_component_ids"], [first["id"]])
        self.assertEqual(sum(item["selected"] for item in result["components"]), 1)
        canonical = Image.open(io.BytesIO(result["canonical_bytes"]))
        self.assertEqual(canonical.size, (1024, 1024))
        self.assertEqual(canonical.getchannel("A").getbbox(), (0, 0, 1024, 1024))

    def test_empty_selection_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "至少保留一个"):
            rembg_preprocess.canonicalize_components(self._matte(), [])

    def test_unknown_component_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "未知前景组件"):
            rembg_preprocess.canonicalize_components(self._matte(), ["cc-99999"])


if __name__ == "__main__":
    unittest.main()
