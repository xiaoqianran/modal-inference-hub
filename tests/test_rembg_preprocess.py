from __future__ import annotations

import io
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

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
        actual = Image.open(io.BytesIO(selected["canonical_bytes"])).convert("RGBA")
        self.assertEqual(actual.size, expected.size)
        self.assertEqual(actual.tobytes(), expected.tobytes())

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

    def test_in_memory_cache_avoids_connected_component_reanalysis(self) -> None:
        matte = self._matte()
        analysis = rembg_preprocess.analyze_components(matte)
        state = {
            **analysis,
            "components": [
                {key: value for key, value in item.items() if key != "label"}
                for item in analysis["components"]
            ],
        }
        first = state["components"][0]["id"]
        rembg_preprocess.clear_selection_cache()
        rembg_preprocess.canonicalize_components(matte, [first], component_state=state)
        with patch(
            "agent.rembg_preprocess._label_components",
            side_effect=AssertionError("cache miss"),
        ):
            result = rembg_preprocess.canonicalize_components(
                matte,
                [first],
                component_state=state,
            )
        self.assertEqual(result["selected_component_ids"], [first])


class ProviderPreferenceTests(unittest.TestCase):
    def test_provider_preference_is_persisted_and_gpu_falls_back_when_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch(
            "agent.rembg_preprocess.rembg_home",
            return_value=Path(temporary),
        ), patch(
            "agent.rembg_preprocess._available_ort_providers",
            return_value=["CPUExecutionProvider"],
        ):
            gpu = rembg_preprocess.set_provider_preference("gpu")
            self.assertEqual(rembg_preprocess.provider_preference(), "gpu")
            self.assertEqual(gpu["provider_preference"], "gpu")
            self.assertEqual(gpu["provider"], "cpu")
            self.assertFalse(gpu["gpu_available"])
            self.assertIn("回退 CPU", gpu["fallback_reason"] or "")

            cpu = rembg_preprocess.set_provider_preference("cpu")
            self.assertEqual(rembg_preprocess.provider_preference(), "cpu")
            self.assertEqual(cpu["provider"], "cpu")
            self.assertIsNone(cpu["fallback_reason"])

    def test_invalid_provider_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "cpu 或 gpu"):
            rembg_preprocess.set_provider_preference("metal")

    def test_gpu_session_activates_cuda_provider_when_available(self) -> None:
        class Inner:
            @staticmethod
            def get_providers():
                return ["CUDAExecutionProvider", "CPUExecutionProvider"]

        class Session:
            inner_session = Inner()

        rembg_preprocess.reset_session()
        with patch("agent.rembg_preprocess.provider_preference", return_value="gpu"), patch(
            "agent.rembg_preprocess._nvidia_gpu_present", return_value=True
        ), patch("onnxruntime.get_available_providers", return_value=["CUDAExecutionProvider", "CPUExecutionProvider"]), patch(
            "rembg.session_factory.new_session", return_value=Session()
        ):
            rembg_preprocess._get_session()
            current = rembg_preprocess.status()
        self.assertEqual(current["provider"], "gpu")
        self.assertIsNone(current["fallback_reason"])
        rembg_preprocess.reset_session()

    def test_gpu_session_failure_falls_back_to_cpu(self) -> None:
        class Inner:
            @staticmethod
            def get_providers():
                return ["CPUExecutionProvider"]

        class Session:
            inner_session = Inner()

        rembg_preprocess.reset_session()
        with patch("agent.rembg_preprocess.provider_preference", return_value="gpu"), patch(
            "agent.rembg_preprocess._nvidia_gpu_present", return_value=True
        ), patch("onnxruntime.get_available_providers", return_value=["CUDAExecutionProvider", "CPUExecutionProvider"]), patch(
            "rembg.session_factory.new_session", side_effect=[RuntimeError("cuda dll missing"), Session()]
        ):
            rembg_preprocess._get_session()
            self.assertEqual(rembg_preprocess._session_provider, "cpu")
            self.assertIn("GPU 初始化失败", rembg_preprocess._session_fallback_reason or "")
        rembg_preprocess.reset_session()


if __name__ == "__main__":
    unittest.main()
