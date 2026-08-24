from __future__ import annotations

import hashlib
import io
import json
import math
import time
import uuid
from collections import OrderedDict
from pathlib import Path

MAX_IMAGE_PIXELS = 40_000_000
MAX_CONCEPT_CHARS = 160
MAX_CANDIDATES = 24
DEFAULT_MAX_CANDIDATES = 16
DEFAULT_OUTPUT_SIZE = 1024
SCENE_CACHE_SIZE = 1
SAM3_COMMIT = "8f0b7f4d4e7eda2ed606ebde6702c93359ad01da"
SAM31_REVISION = "daa63191845a41281374e725f4c9e51c7a824460"


def _hex_id(value: str, length: int, name: str) -> str:
    value = str(value)
    if len(value) != length or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"invalid {name}")
    return value


def _candidate_id(value: str) -> str:
    value = str(value)
    if len(value) != 3 or value[0] != "c" or not value[1:].isdigit():
        raise ValueError("invalid candidate_id")
    return value


def _validate_concept(concept: str) -> str:
    concept = " ".join(str(concept).split())
    if not concept:
        raise ValueError("concept must not be empty")
    if len(concept) > MAX_CONCEPT_CHARS:
        raise ValueError(f"concept exceeds {MAX_CONCEPT_CHARS} characters")
    return concept


def _validate_max_candidates(value: int) -> int:
    value = int(value)
    if not 1 <= value <= MAX_CANDIDATES:
        raise ValueError(f"max_candidates must be between 1 and {MAX_CANDIDATES}")
    return value


def _validate_output_size(value: int) -> int:
    value = int(value)
    if not 256 <= value <= 2048:
        raise ValueError("output_size must be between 256 and 2048")
    return value


def _box_from_dict(raw: dict) -> tuple[list[float], bool]:
    try:
        cx, cy = float(raw["cx"]), float(raw["cy"])
        width, height = float(raw["width"]), float(raw["height"])
        positive = bool(raw.get("positive", True))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("box must contain numeric cx, cy, width, height") from exc
    values = (cx, cy, width, height)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("box values must be finite")
    if not (0 <= cx <= 1 and 0 <= cy <= 1 and 0 < width <= 1 and 0 < height <= 1):
        raise ValueError("box coordinates must be normalized to [0, 1]")
    if cx - width / 2 < 0 or cx + width / 2 > 1 or cy - height / 2 < 0 or cy + height / 2 > 1:
        raise ValueError("box must stay inside the image")
    return [cx, cy, width, height], positive


def _decode_image(image_bytes: bytes):
    from PIL import Image, ImageOps

    if not image_bytes:
        raise ValueError("image is empty")
    image = Image.open(io.BytesIO(image_bytes))
    image.load()
    image = ImageOps.exif_transpose(image)
    if image.width * image.height > MAX_IMAGE_PIXELS:
        raise ValueError(f"image exceeds {MAX_IMAGE_PIXELS} pixels")
    if image.mode in ("RGBA", "LA") or "transparency" in image.info:
        rgba = image.convert("RGBA")
        rgb = Image.new("RGB", rgba.size, (255, 255, 255))
        rgb.paste(rgba, mask=rgba.getchannel("A"))
        return rgb
    return image.convert("RGB")


def _canonical_rgba(image, mask, output_size: int, padding_ratio: float = 0.08):
    import numpy as np
    from PIL import Image

    mask = np.asarray(mask, dtype=bool).squeeze()
    if mask.ndim != 2 or mask.shape != (image.height, image.width):
        raise ValueError("mask shape does not match image")
    ys, xs = np.nonzero(mask)
    if not len(xs):
        raise ValueError("mask is empty")

    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    object_side = max(x1 - x0, y1 - y0)
    side = max(2, math.ceil(object_side * (1 + 2 * padding_ratio)))
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    left, top = math.floor(cx - side / 2), math.floor(cy - side / 2)
    right, bottom = left + side, top + side

    src_left, src_top = max(0, left), max(0, top)
    src_right, src_bottom = min(image.width, right), min(image.height, bottom)
    dst_left, dst_top = src_left - left, src_top - top

    rgb = np.asarray(image, dtype=np.uint8)
    alpha = mask.astype(np.uint8) * 255
    rgba = Image.fromarray(np.dstack([rgb, alpha]), "RGBA")
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    crop = rgba.crop((src_left, src_top, src_right, src_bottom))
    canvas.paste(crop, (dst_left, dst_top), crop)
    if canvas.size != (output_size, output_size):
        canvas = canvas.resize((output_size, output_size), Image.Resampling.LANCZOS)
    return canvas, {
        "source_bbox_xyxy": [x0, y0, x1, y1],
        "source_mask_fraction": float(mask.mean()),
        "padding_ratio": padding_ratio,
        "output_size": output_size,
    }


class SamRuntime:
    def __init__(self, root: Path, checkpoint: Path) -> None:
        import sam3.model_builder as sam3_builder
        import torch
        from sam3.model.sam3_image_processor import Sam3Processor

        if not checkpoint.is_file():
            raise FileNotFoundError(checkpoint)
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.checkpoint = checkpoint
        self.scenes = self.root / "scenes"
        self.selections = self.root / "selections"
        self.scenes.mkdir(exist_ok=True)
        self.selections.mkdir(exist_ok=True)

        torch.set_float32_matmul_precision("high")
        torch.cuda.reset_peak_memory_stats()
        started = time.perf_counter()
        self.model = sam3_builder.build_sam3_image_model(
            device="cpu",
            checkpoint_path=None,
            load_from_HF=False,
            enable_inst_interactivity=False,
            compile=False,
        )
        visual = self.model.backbone.vision_backbone
        if len(visual.convs) != 4 or self.model.backbone.scalp != 1:
            raise RuntimeError("unexpected SAM 3 image-backbone layout")
        visual.convs = torch.nn.ModuleList(list(visual.convs[:3]))
        visual.scale_factors = visual.scale_factors[:3]
        self.model.backbone.scalp = 0
        sam3_builder._load_checkpoint(self.model, str(checkpoint))
        self.model = self.model.cuda().eval()
        self.processor = Sam3Processor(self.model, confidence_threshold=0.5)
        torch.cuda.synchronize()
        self.load_s = time.perf_counter() - started
        self.scene_cache: OrderedDict[str, tuple[object, dict]] = OrderedDict()

    def health(self) -> dict:
        import torch

        return {
            "ok": True,
            "ready": True,
            "gpu": torch.cuda.get_device_name(),
            "vram_gib": torch.cuda.get_device_properties(0).total_memory / 2**30,
            "bf16": bool(torch.cuda.is_bf16_supported()),
            "model_load_s": self.load_s,
            "sam3_code_commit": SAM3_COMMIT,
            "sam31_revision": SAM31_REVISION,
        }

    def _scene_path(self, scene_id: str) -> Path:
        return self.scenes / _hex_id(scene_id, 64, "scene_id") / "input.bin"

    def _selection_root(self, scene_id: str, selection_id: str) -> Path:
        return self.selections / _hex_id(scene_id, 64, "scene_id") / _hex_id(
            selection_id, 32, "selection_id"
        )

    @staticmethod
    def _timed(fn):
        import torch

        torch.cuda.synchronize()
        started = time.perf_counter()
        value = fn()
        torch.cuda.synchronize()
        return value, time.perf_counter() - started

    def _store_scene(self, scene_id: str, image_bytes: bytes) -> None:
        path = self._scene_path(scene_id)
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(image_bytes)

    def _image_for_scene(self, scene_id: str):
        cached = self.scene_cache.get(scene_id)
        if cached is not None:
            return cached[0]
        path = self._scene_path(scene_id)
        if not path.is_file():
            raise FileNotFoundError(f"scene not found: {scene_id}")
        return _decode_image(path.read_bytes())

    def _state_for(self, scene_id: str, image):
        import torch

        cached = self.scene_cache.pop(scene_id, None)
        if cached is not None:
            self.scene_cache[scene_id] = cached
            return cached[1], 0.0, True
        with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
            state, encode_s = self._timed(lambda: self.processor.set_image(image))
        self.scene_cache[scene_id] = (image, state)
        while len(self.scene_cache) > SCENE_CACHE_SIZE:
            self.scene_cache.popitem(last=False)
            torch.cuda.empty_cache()
        return state, encode_s, False

    def _persist_masks(
        self,
        image,
        output,
        scene_id: str,
        concept: str,
        kind: str,
        max_candidates: int,
    ) -> dict:
        import numpy as np

        scores = output["scores"].detach().float().cpu().numpy()
        boxes = output["boxes"].detach().float().cpu().numpy()
        masks = output["masks"].detach().cpu().numpy()
        order = np.argsort(-scores)[:max_candidates]

        selection_id = uuid.uuid4().hex
        root = self._selection_root(scene_id, selection_id)
        root.mkdir(parents=True, exist_ok=True)
        candidates = []
        stored_masks = []
        for rank, index in enumerate(order):
            mask = np.asarray(masks[index]).squeeze().astype(bool)
            if not mask.any():
                continue
            mask_index = len(stored_masks)
            stored_masks.append(mask)
            candidate = f"c{rank:02d}"
            ys, xs = np.nonzero(mask)
            box = [float(value) for value in boxes[index]]
            candidates.append(
                {
                    "candidate_id": candidate,
                    "rank": rank,
                    "mask_index": mask_index,
                    "score": float(scores[index]),
                    "mask_pixels": int(mask.sum()),
                    "mask_fraction": float(mask.mean()),
                    "mask_bbox_xyxy": [
                        int(xs.min()),
                        int(ys.min()),
                        int(xs.max()) + 1,
                        int(ys.max()) + 1,
                    ],
                    "model_bbox_xyxy": box,
                    "model_bbox_xyxy_norm": [
                        min(1.0, max(0.0, box[0] / image.width)),
                        min(1.0, max(0.0, box[1] / image.height)),
                        min(1.0, max(0.0, box[2] / image.width)),
                        min(1.0, max(0.0, box[3] / image.height)),
                    ],
                }
            )

        if stored_masks:
            flat = np.stack(stored_masks, axis=0).reshape(len(stored_masks), -1)
            packed = np.packbits(flat, axis=1, bitorder="little")
            masks_path = root / "masks.bin"
            masks_path.write_bytes(packed.tobytes())
            mask_storage = {
                "encoding": "numpy.packbits",
                "bitorder": "little",
                "shape": [image.height, image.width],
                "count": len(stored_masks),
                "bytes_per_mask": int(packed.shape[1]),
                "bytes": masks_path.stat().st_size,
            }
        else:
            mask_storage = {
                "encoding": "numpy.packbits",
                "bitorder": "little",
                "shape": [image.height, image.width],
                "count": 0,
                "bytes_per_mask": 0,
                "bytes": 0,
            }

        result = {
            "scene_id": scene_id,
            "selection_id": selection_id,
            "concept": concept,
            "kind": kind,
            "image_size": [image.width, image.height],
            "candidate_count": len(candidates),
            "mask_storage": mask_storage,
            "candidates": candidates,
        }
        (root / "result.json").write_text(json.dumps(result, separators=(",", ":")))
        return result

    def segment(self, image_path: Path, concept: str, max_candidates: int) -> dict:
        import torch

        concept = _validate_concept(concept)
        max_candidates = _validate_max_candidates(max_candidates)
        image_bytes = image_path.read_bytes()
        image = _decode_image(image_bytes)
        scene_id = hashlib.sha256(image_bytes).hexdigest()
        self._store_scene(scene_id, image_bytes)
        state, encode_s, cache_hit = self._state_for(scene_id, image)

        torch.cuda.reset_peak_memory_stats()
        with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
            self.processor.reset_all_prompts(state)
            output, prompt_s = self._timed(
                lambda: self.processor.set_text_prompt(prompt=concept, state=state)
            )
        result = self._persist_masks(image, output, scene_id, concept, "text", max_candidates)
        result.update(
            {
                "gpu": torch.cuda.get_device_name(),
                "model_load_s": self.load_s,
                "encode_s": encode_s,
                "scene_cache_hit": cache_hit,
                "prompt_s": prompt_s,
                "peak_allocated_gib": torch.cuda.max_memory_allocated() / 2**30,
                "peak_reserved_gib": torch.cuda.max_memory_reserved() / 2**30,
                "sam3_code_commit": SAM3_COMMIT,
                "sam31_revision": SAM31_REVISION,
            }
        )
        root = self._selection_root(scene_id, result["selection_id"])
        (root / "result.json").write_text(json.dumps(result, separators=(",", ":")))
        return result

    def refine(self, scene_id: str, concept: str, boxes: list[dict], max_candidates: int) -> dict:
        import torch

        concept = _validate_concept(concept)
        max_candidates = _validate_max_candidates(max_candidates)
        if not boxes or len(boxes) > 16:
            raise ValueError("boxes must contain between 1 and 16 prompts")
        parsed_boxes = [_box_from_dict(box) for box in boxes]
        image = self._image_for_scene(scene_id)
        state, encode_s, cache_hit = self._state_for(scene_id, image)

        torch.cuda.reset_peak_memory_stats()
        box_timings = []
        with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
            self.processor.reset_all_prompts(state)
            output, text_s = self._timed(
                lambda: self.processor.set_text_prompt(prompt=concept, state=state)
            )
            for box, positive in parsed_boxes:
                output, elapsed = self._timed(
                    lambda current=box, label=positive: self.processor.add_geometric_prompt(
                        box=current, label=label, state=state
                    )
                )
                box_timings.append(elapsed)

        result = self._persist_masks(image, output, scene_id, concept, "refine", max_candidates)
        result.update(
            {
                "gpu": torch.cuda.get_device_name(),
                "model_load_s": self.load_s,
                "encode_s": encode_s,
                "scene_cache_hit": cache_hit,
                "text_prompt_s": text_s,
                "box_prompt_s": box_timings,
                "prompt_s": text_s + sum(box_timings),
                "boxes": [
                    {
                        "cx": box[0],
                        "cy": box[1],
                        "width": box[2],
                        "height": box[3],
                        "positive": positive,
                    }
                    for box, positive in parsed_boxes
                ],
                "peak_allocated_gib": torch.cuda.max_memory_allocated() / 2**30,
                "peak_reserved_gib": torch.cuda.max_memory_reserved() / 2**30,
                "sam3_code_commit": SAM3_COMMIT,
                "sam31_revision": SAM31_REVISION,
            }
        )
        root = self._selection_root(scene_id, result["selection_id"])
        (root / "result.json").write_text(json.dumps(result, separators=(",", ":")))
        return result

    def materialize(
        self,
        scene_id: str,
        selection_id: str,
        candidate_id: str,
        output_size: int = DEFAULT_OUTPUT_SIZE,
    ) -> dict:
        import numpy as np
        from PIL import Image

        output_size = _validate_output_size(output_size)
        candidate_id = _candidate_id(candidate_id)
        scene_path = self._scene_path(scene_id)
        root = self._selection_root(scene_id, selection_id)
        result_path = root / "result.json"
        masks_path = root / "masks.bin"
        if not scene_path.is_file():
            raise FileNotFoundError(f"scene not found: {scene_id}")
        if not result_path.is_file() or not masks_path.is_file():
            raise FileNotFoundError(f"selection not found: {selection_id}")

        result = json.loads(result_path.read_text())
        candidate = next(
            (item for item in result.get("candidates", []) if item.get("candidate_id") == candidate_id),
            None,
        )
        if candidate is None:
            raise FileNotFoundError(f"candidate not found: {candidate_id}")

        height, width = result["mask_storage"]["shape"]
        bytes_per_mask = int(result["mask_storage"]["bytes_per_mask"])
        mask_index = int(candidate["mask_index"])
        packed = masks_path.read_bytes()
        begin, end = mask_index * bytes_per_mask, (mask_index + 1) * bytes_per_mask
        if end > len(packed):
            raise ValueError("packed mask storage is truncated")
        mask = np.unpackbits(
            np.frombuffer(packed[begin:end], dtype=np.uint8),
            count=height * width,
            bitorder="little",
        ).reshape(height, width).astype(bool)

        image = _decode_image(scene_path.read_bytes())
        if image.size != (width, height):
            raise ValueError("scene dimensions do not match mask storage")

        candidate_root = root / candidate_id
        candidate_root.mkdir(parents=True, exist_ok=True)
        mask_path = candidate_root / "mask.png"
        canonical_path = candidate_root / "canonical.png"
        Image.fromarray(mask.astype(np.uint8) * 255, "L").save(mask_path, compress_level=1)
        canonical, metadata = _canonical_rgba(image, mask, output_size)
        canonical.save(canonical_path, compress_level=1)
        return {
            "scene_id": scene_id,
            "selection_id": selection_id,
            "candidate_id": candidate_id,
            "mask_file": str(mask_path),
            "canonical_file": str(canonical_path),
            "mask_bytes": mask_path.stat().st_size,
            "canonical_bytes": canonical_path.stat().st_size,
            "canonical": metadata,
        }
