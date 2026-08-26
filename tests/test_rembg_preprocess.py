from __future__ import annotations

import io
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from PIL import Image

from agent import rembg_preprocess
from agent.preprocess import image_ops, model_store, runtime


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
        with patch("agent.preprocess.runtime.predict_mask", return_value=(mask, "gpu", None)):
            result = rembg_preprocess.process(self._source())
        canonical = Image.open(io.BytesIO(result["canonical_bytes"]))
        self.assertEqual(canonical.mode, "RGBA")
        self.assertEqual(canonical.size, (1024, 1024))
        alpha = canonical.getchannel("A")
        self.assertEqual(alpha.getbbox(), (0, 256, 1024, 768))
        self.assertEqual(result["foreground_bbox"], [100, 100, 700, 400])
        self.assertEqual(result["engine"], "birefnet-general-lite")
        self.assertEqual(result["provider"], "gpu")

    def test_no_foreground_is_rejected(self) -> None:
        mask = Image.new("L", (800, 400), 0)
        with (
            patch("agent.preprocess.runtime.predict_mask", return_value=(mask, "gpu", None)),
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
            expected = image_ops._letterbox_rgba(
                rgba.convert("RGBA"),
                image_ops._foreground_bbox(rgba.getchannel("A")),
            )
        actual = Image.open(io.BytesIO(selected["canonical_bytes"])).convert("RGBA")
        self.assertEqual(actual.size, expected.size)
        self.assertEqual(actual.tobytes(), expected.tobytes())
        self.assertEqual(selected["canonical_bytes"], image_ops._png_bytes(expected))

    def test_selection_preview_hides_unselected_objects_in_source_coordinates(self) -> None:
        matte = self._matte()
        analysis = rembg_preprocess.analyze_components(matte)
        first = analysis["components"][0]
        result = rembg_preprocess.canonicalize_components(matte, [first["id"]])
        preview = Image.open(io.BytesIO(result["selection_bytes"])).convert("RGBA")
        self.assertEqual(preview.size, (400, 200))
        self.assertEqual(preview.getchannel("A").getbbox(), tuple(first["bbox"]))

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
            "agent.preprocess.image_ops._label_components",
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
            "agent.preprocess.model_store.rembg_home",
            return_value=Path(temporary),
        ), patch(
            "agent.preprocess.runtime._available_ort_providers",
            return_value=["CPUExecutionProvider"],
        ), patch("agent.preprocess.runtime.warmup_gpu_async"):
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

    def test_gpu_is_default_when_no_preference_file_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch(
            "agent.preprocess.model_store.rembg_home", return_value=Path(temporary)
        ):
            self.assertEqual(runtime.provider_preference(), "gpu")

    def test_cpu_preference_disables_gpu_warmup(self) -> None:
        with patch("agent.preprocess.runtime.provider_preference", return_value="cpu"), patch(
            "agent.preprocess.runtime._get_session"
        ) as get_session:
            self.assertFalse(runtime.warmup_gpu_async())
            get_session.assert_not_called()

    def test_gpu_warmup_keeps_cuda_session_resident(self) -> None:
        rembg_preprocess.reset_session()

        def warm_session():
            runtime._session = object()
            runtime._session_provider = "gpu"
            runtime._session_ort_provider = "CUDAExecutionProvider"
            return runtime._session

        with patch("agent.preprocess.runtime.provider_preference", return_value="gpu"), patch(
            "agent.preprocess.runtime._get_session", side_effect=warm_session
        ):
            self.assertTrue(runtime.warmup_gpu_async())
            thread = runtime._warmup_thread
            self.assertIsNotNone(thread)
            thread.join(timeout=1)
            self.assertFalse(thread.is_alive())
            self.assertTrue(runtime.status()["gpu_warm"])
            self.assertIsNotNone(runtime._session)

        rembg_preprocess.reset_session()

    def test_switching_to_cpu_releases_warm_gpu_session(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch(
            "agent.preprocess.model_store.rembg_home", return_value=Path(temporary)
        ), patch("agent.preprocess.runtime._available_ort_providers", return_value=["CPUExecutionProvider"]):
            runtime._session = object()
            runtime._session_provider = "gpu"
            runtime._session_ort_provider = "CUDAExecutionProvider"
            result = rembg_preprocess.set_provider_preference("cpu")
            self.assertIsNone(runtime._session)
            self.assertEqual(result["provider_preference"], "cpu")
            self.assertFalse(result["gpu_warm"])

    def test_session_build_discards_stale_gpu_when_preference_changes(self) -> None:
        preference = {"value": "gpu"}
        built: list[str] = []

        def build(provider: str):
            built.append(provider)
            if provider == "gpu":
                preference["value"] = "cpu"
                return object(), "gpu", "CUDAExecutionProvider", None
            return object(), "cpu", "CPUExecutionProvider", None

        rembg_preprocess.reset_session()
        with patch("agent.preprocess.runtime.provider_preference", side_effect=lambda: preference["value"]), patch(
            "agent.preprocess.model_store.ensure_model_ready"
        ), patch("agent.preprocess.runtime._build_session", side_effect=build):
            runtime._get_session()

        self.assertEqual(built, ["gpu", "cpu"])
        self.assertEqual(runtime._session_provider, "cpu")
        self.assertEqual(runtime._session_ort_provider, "CPUExecutionProvider")
        rembg_preprocess.reset_session()

    def test_gpu_session_activates_cuda_provider_when_available(self) -> None:
        class Inner:
            @staticmethod
            def get_providers():
                return ["CUDAExecutionProvider", "CPUExecutionProvider"]

        class Session:
            inner_session = Inner()

        rembg_preprocess.reset_session()
        with patch("agent.preprocess.runtime.provider_preference", return_value="gpu"), patch(
            "onnxruntime.get_available_providers",
            return_value=["CUDAExecutionProvider", "CPUExecutionProvider"],
        ), patch("agent.preprocess.runtime._preload_cuda_runtime"), patch(
            "rembg.session_factory.new_session", return_value=Session()
        ):
            runtime._get_session()
            current = rembg_preprocess.status()
        self.assertEqual(current["provider"], "gpu")
        self.assertIsNone(current["fallback_reason"])
        rembg_preprocess.reset_session()

    def test_cuda_session_uses_provider_options_and_preloads_runtime(self) -> None:
        class Inner:
            @staticmethod
            def get_providers():
                return ["CUDAExecutionProvider", "CPUExecutionProvider"]

        class Session:
            inner_session = Inner()

        captured = {}

        def fake_new_session(_model, *, sess_opts, providers):
            captured["enable_cpu_mem_arena"] = sess_opts.enable_cpu_mem_arena
            captured["providers"] = list(providers)
            return Session()

        rembg_preprocess.reset_session()
        preload = patch("agent.preprocess.runtime._preload_cuda_runtime")
        with patch("agent.preprocess.runtime.provider_preference", return_value="gpu"), patch(
            "onnxruntime.get_available_providers",
            return_value=["CUDAExecutionProvider", "CPUExecutionProvider"],
        ), preload as preload_cuda, patch(
            "rembg.session_factory.new_session", side_effect=fake_new_session
        ):
            runtime._get_session()
        self.assertFalse(captured["enable_cpu_mem_arena"])
        self.assertEqual(captured["providers"][0][0], "CUDAExecutionProvider")
        self.assertEqual(captured["providers"][0][1]["device_id"], 0)
        self.assertEqual(captured["providers"][0][1]["cudnn_conv_use_max_workspace"], 0)
        self.assertEqual(captured["providers"][0][1]["cudnn_conv_algo_search"], "HEURISTIC")
        preload_cuda.assert_called_once()
        rembg_preprocess.reset_session()

    def test_gpu_session_failure_falls_back_to_cpu(self) -> None:
        class Inner:
            @staticmethod
            def get_providers():
                return ["CPUExecutionProvider"]

        class Session:
            inner_session = Inner()

        rembg_preprocess.reset_session()
        with patch("agent.preprocess.runtime.provider_preference", return_value="gpu"), patch(
            "onnxruntime.get_available_providers",
            return_value=["CUDAExecutionProvider", "CPUExecutionProvider"],
        ), patch("agent.preprocess.runtime._preload_cuda_runtime"), patch(
            "rembg.session_factory.new_session", side_effect=[RuntimeError("cuda init failed"), Session()]
        ):
            runtime._get_session()
            self.assertEqual(runtime._session_provider, "cpu")
            self.assertIn("CUDA 初始化失败", runtime._session_fallback_reason or "")
        rembg_preprocess.reset_session()

    def test_gpu_inference_decode_failure_releases_gpu_and_retries_on_cpu(self) -> None:
        mask = Image.new("L", (16, 16), 255)

        class GpuSession:
            @staticmethod
            def predict(_image):
                raise UnicodeDecodeError("utf-8", b"\xc4", 0, 1, "invalid continuation byte")

        class CpuSession:
            @staticmethod
            def predict(_image):
                return [mask]

        rembg_preprocess.reset_session()
        runtime._session = GpuSession()
        runtime._session_provider = "gpu"
        with patch("agent.preprocess.runtime._new_cpu_session", return_value=CpuSession()):
            result = runtime._predict_mask(Image.new("RGB", (16, 16)))

        self.assertEqual(result.getbbox(), (0, 0, 16, 16))
        self.assertEqual(runtime._session_provider, "cpu")
        self.assertIn("显存不足或驱动执行失败", runtime._session_fallback_reason or "")
        rembg_preprocess.reset_session()

    def test_predict_mask_releases_cpu_session_after_one_shot_inference(self) -> None:
        mask = Image.new("L", (800, 400), 255)

        def predict(_image):
            runtime._session = object()
            runtime._session_provider = "gpu"
            runtime._session_ort_provider = "CPUExecutionProvider"
            return mask

        rembg_preprocess.reset_session()
        with patch("agent.preprocess.runtime._predict_mask", side_effect=predict):
            actual, provider, fallback = runtime.predict_mask(Image.new("RGB", (800, 400)))

        self.assertEqual(actual.size, mask.size)
        self.assertEqual(provider, "gpu")
        self.assertIsNone(fallback)
        self.assertIsNone(runtime._session)
        rembg_preprocess.reset_session()


class ModelDownloadTests(unittest.TestCase):
    class FakeResponse:
        def __init__(self, data: bytes, status: int = 200, fail_after: int | None = None):
            self.data = data
            self.status = status
            self.offset = 0
            self.fail_after = fail_after

        def getcode(self):
            return self.status

        def read(self, size: int) -> bytes:
            if self.fail_after is not None and self.offset >= self.fail_after:
                raise ConnectionError("network interrupted")
            if self.offset >= len(self.data):
                return b""
            end = min(len(self.data), self.offset + size)
            chunk = self.data[self.offset:end]
            self.offset = end
            return chunk

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def setUp(self) -> None:
        rembg_preprocess.reset_session()
        model_store._verified_model_signature = None
        model_store._set_download_state(
            status="idle",
            downloaded_bytes=0,
            total_bytes=model_store.MODEL_BYTES,
            error=None,
            integrity="unverified",
        )

    def test_interrupted_download_resumes_with_range_and_verifies(self) -> None:
        payload = b"abcdefghij" * 100
        digest = __import__("hashlib").md5(payload).hexdigest()
        requests = []
        first_response = self.FakeResponse(payload[:400], status=200, fail_after=400)

        def first_urlopen(request, timeout):
            requests.append(request)
            return first_response

        with tempfile.TemporaryDirectory() as temporary, patch(
            "agent.preprocess.model_store.rembg_home", return_value=Path(temporary)
        ), patch.object(model_store, "MODEL_BYTES", len(payload)), patch.object(
            model_store, "MODEL_MD5", digest
        ), patch("urllib.request.urlopen", side_effect=first_urlopen):
            with self.assertRaisesRegex(RuntimeError, "可继续重试"):
                model_store.ensure_model_ready()
            partial = model_store.partial_model_path()
            self.assertEqual(partial.read_bytes(), payload[:400])
            self.assertTrue(model_store.download_status()["resumable"])

            def second_urlopen(request, timeout):
                requests.append(request)
                self.assertEqual(request.headers.get("Range"), "bytes=400-")
                return self.FakeResponse(payload[400:], status=206)

            with patch("urllib.request.urlopen", side_effect=second_urlopen):
                path = model_store.ensure_model_ready()
            self.assertEqual(path.read_bytes(), payload)
            state = model_store.download_status()
            self.assertEqual(state["status"], "ready")
            self.assertEqual(state["integrity"], "verified")
            self.assertEqual(state["progress"], 1.0)

    def test_complete_partial_is_verified_without_network_request(self) -> None:
        payload = b"complete partial model"
        digest = __import__("hashlib").md5(payload).hexdigest()
        with tempfile.TemporaryDirectory() as temporary, patch(
            "agent.preprocess.model_store.rembg_home", return_value=Path(temporary)
        ), patch.object(model_store, "MODEL_BYTES", len(payload)), patch.object(
            model_store, "MODEL_MD5", digest
        ):
            partial = model_store.partial_model_path()
            partial.parent.mkdir(parents=True, exist_ok=True)
            partial.write_bytes(payload)
            with patch("urllib.request.urlopen", side_effect=AssertionError("network should not be used")):
                path = model_store.ensure_model_ready()
            self.assertEqual(path.read_bytes(), payload)
            self.assertFalse(partial.exists())
            self.assertEqual(model_store.download_status()["integrity"], "verified")

    def test_prepare_model_async_deduplicates_background_worker(self) -> None:
        import threading

        started = threading.Event()
        release = threading.Event()
        calls = []

        def fake_prepare():
            calls.append(1)
            started.set()
            release.wait(timeout=2)
            return Path("fake.onnx")

        with tempfile.TemporaryDirectory() as temporary, patch(
            "agent.preprocess.model_store.rembg_home", return_value=Path(temporary)
        ), patch("agent.preprocess.model_store.ensure_model_ready", side_effect=fake_prepare):
            model_store._prepare_thread = None
            model_store.prepare_model_async()
            self.assertTrue(started.wait(timeout=1))
            first = model_store._prepare_thread
            model_store.prepare_model_async()
            second = model_store._prepare_thread
            self.assertIs(first, second)
            self.assertEqual(len(calls), 1)
            release.set()
            if first is not None:
                first.join(timeout=2)
            self.assertEqual(len(calls), 1)

    def test_bad_checksum_deletes_completed_partial(self) -> None:
        payload = b"broken model bytes"
        with tempfile.TemporaryDirectory() as temporary, patch(
            "agent.preprocess.model_store.rembg_home", return_value=Path(temporary)
        ), patch.object(model_store, "MODEL_BYTES", len(payload)), patch.object(
            model_store, "MODEL_MD5", "0" * 32
        ), patch(
            "urllib.request.urlopen",
            return_value=self.FakeResponse(payload),
        ):
            with self.assertRaisesRegex(RuntimeError, "MD5"):
                model_store.ensure_model_ready()
            self.assertFalse(model_store.partial_model_path().exists())
            self.assertFalse(model_store.model_path().exists())
            state = model_store.download_status()
            self.assertEqual(state["status"], "failed")
            self.assertEqual(state["integrity"], "failed")
            self.assertFalse(state["resumable"])


if __name__ == "__main__":
    unittest.main()
