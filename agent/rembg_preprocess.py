"""Local-only image matting and canonicalization for modal-3D.

The source image never leaves the machine during preprocessing. The active V1
pipeline is intentionally small: rembg/birefnet-general produces one global
alpha matte, then the foreground bounding box is letterboxed to the cloud
contract (1024x1024, 8-bit RGBA PNG).
"""

from __future__ import annotations

import hashlib
import io
import os
import threading
import time
from pathlib import Path

from PIL import Image, ImageOps

from agent.storage import data_dir

ENGINE = "birefnet-general"
CANONICAL_SIZE = 1024
_BBOX_ALPHA_THRESHOLD = 8
_session_lock = threading.RLock()
_session = None


def rembg_home() -> Path:
    path = data_dir() / "rembg"
    path.mkdir(parents=True, exist_ok=True)
    return path


def model_path() -> Path:
    return rembg_home() / "models" / ENGINE / f"{ENGINE}.onnx"


def status() -> dict:
    model = model_path()
    return {
        "engine": ENGINE,
        "provider": "cpu",
        "model_home": str(rembg_home()),
        "model_path": str(model),
        "model_downloaded": model.is_file() and model.stat().st_size > 0,
        "canonical_size": CANONICAL_SIZE,
        "cpu_threads": _available_cpu_threads(),
        "local_only": True,
    }


def _available_cpu_threads() -> int:
    try:
        available = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        available = os.cpu_count() or 4
    return max(1, min(8, available))


def _get_session():
    global _session
    with _session_lock:
        if _session is None:
            # Keep the ~1 GB model inside modal-3D-client app-data, not ~/.u2net.
            os.environ.setdefault("U2NET_HOME", str(rembg_home()))
            import onnxruntime as ort
            from rembg.session_factory import new_session

            options = ort.SessionOptions()
            options.intra_op_num_threads = _available_cpu_threads()
            options.inter_op_num_threads = 1
            _session = new_session(
                ENGINE,
                sess_opts=options,
                providers=["CPUExecutionProvider"],
            )
        return _session


def _predict_mask(image: Image.Image) -> Image.Image:
    masks = _get_session().predict(image.convert("RGB"))
    if not masks:
        raise RuntimeError("rembg 未返回前景 Alpha 掩码")
    return masks[0].convert("L")


def _foreground_bbox(mask: Image.Image) -> tuple[int, int, int, int]:
    binary = mask.point(lambda value: 255 if value > _BBOX_ALPHA_THRESHOLD else 0)
    bbox = binary.getbbox()
    if bbox is None:
        raise ValueError("rembg 未检测到可用前景")
    return bbox


def _letterbox_rgba(
    rgba: Image.Image,
    bbox: tuple[int, int, int, int],
    size: int = CANONICAL_SIZE,
) -> Image.Image:
    crop = rgba.crop(bbox)
    width, height = crop.size
    if width <= 0 or height <= 0:
        raise ValueError("前景包围盒无效")
    scale = min(size / width, size / height)
    target = (max(1, round(width * scale)), max(1, round(height * scale)))
    resized = crop.resize(target, Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    offset = ((size - target[0]) // 2, (size - target[1]) // 2)
    canvas.alpha_composite(resized, offset)
    return canvas


def _png_bytes(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG", compress_level=6)
    return output.getvalue()


def process(data: bytes) -> dict:
    """Run global local matting and return full matte + canonical PNG bytes."""
    started = time.perf_counter()
    with Image.open(io.BytesIO(data)) as opened:
        source = ImageOps.exif_transpose(opened).convert("RGB")
    mask = _predict_mask(source)
    if mask.size != source.size:
        mask = mask.resize(source.size, Image.Resampling.LANCZOS)

    rgba = source.convert("RGBA")
    rgba.putalpha(mask)
    bbox = _foreground_bbox(mask)
    canonical = _letterbox_rgba(rgba, bbox)
    matte_bytes = _png_bytes(rgba)
    canonical_bytes = _png_bytes(canonical)

    histogram = mask.histogram()
    foreground_pixels = sum(histogram[_BBOX_ALPHA_THRESHOLD + 1 :])
    total_pixels = source.width * source.height
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    return {
        "matte_bytes": matte_bytes,
        "canonical_bytes": canonical_bytes,
        "matte_sha256": hashlib.sha256(matte_bytes).hexdigest(),
        "canonical_sha256": hashlib.sha256(canonical_bytes).hexdigest(),
        "source_size": [source.width, source.height],
        "foreground_bbox": list(bbox),
        "foreground_ratio": foreground_pixels / total_pixels if total_pixels else 0.0,
        "canonical_size": [CANONICAL_SIZE, CANONICAL_SIZE],
        "engine": ENGINE,
        "provider": "cpu",
        "elapsed_ms": elapsed_ms,
    }
