"""One-click deploy of the cloud rembg app (modal-3d-rembg) from the desktop agent.

A new user should not have to clone the separate worker repo or run
`modal deploy` by hand to use cloud background removal. The rembg cloud app is
fully self-contained (it installs rembg + onnxruntime-gpu and bakes the model
into the image at build time), so the agent defines it inline and deploys it
with the already-connected Modal client.

Modal requires decorated functions/classes to live at module global scope (it
imports them by qualified name), so the app is defined at import time here —
mirroring `modal_3d/rembg_gateway.py`. The deployed app honours the client
contract (`mask_bytes_b64`) that `agent.rembg_preprocess._cloud_process` reads.
"""
from __future__ import annotations

import modal

from agent import modal_client

APP_NAME = "modal-3d-rembg"
ENGINE = "birefnet-general-lite"
MODEL_URL = (
    "https://github.com/danielgatis/rembg/releases/download/v0.0.0/"
    "BiRefNet-general-bb_swin_v1_tiny-epoch_232.onnx"
)
MODEL_DIR = "/models/models/birefnet-general-lite"
MODEL_PATH = f"{MODEL_DIR}/birefnet-general-lite.onnx"

app = modal.App(APP_NAME)

image = (
    modal.Image.debian_slim(python_version="3.11")
    .uv_pip_install(
        "fastapi==0.116.1",
        "rembg==2.0.81",
        "onnxruntime-gpu==1.25.1",
        "nvidia-cublas-cu12==12.9.2.10",
        "nvidia-cuda-runtime-cu12==12.9.79",
        "nvidia-cudnn-cu12==9.24.0.43",
        "nvidia-cufft-cu12==11.4.1.4",
        "nvidia-curand-cu12==10.3.10.19",
        "Pillow",
    )
    .run_commands(
        f"python -c \"import urllib.request, os; os.makedirs('{MODEL_DIR}', exist_ok=True); "
        f"urllib.request.urlretrieve('{MODEL_URL}', '{MODEL_PATH}')\""
    )
    .env({"U2NET_HOME": "/models"})
)


@app.cls(image=image, gpu="T4", timeout=10 * 60, scaledown_window=300)
class RemBgWorker:
    @modal.enter()
    def load(self) -> None:
        import onnxruntime as ort

        if hasattr(ort, "preload_dlls"):
            ort.preload_dlls(cuda=True, cudnn=True, directory="")
        from rembg.session_factory import new_session

        self.session = new_session(
            ENGINE, providers=["CUDAExecutionProvider", "CPUExecutionProvider"]
        )

    @modal.method()
    def process(self, data: bytes) -> dict:
        import base64
        import io
        import time

        from PIL import Image, ImageOps

        started = time.perf_counter()
        with Image.open(io.BytesIO(data)) as opened:
            source = ImageOps.exif_transpose(opened).convert("RGB")
        prediction = self.session.predict(source)
        if not prediction:
            raise RuntimeError("rembg returned no foreground alpha mask")
        mask = prediction[0]
        if mask.mode != "L":
            mask = mask.convert("L")
        if mask.size != source.size:
            mask = mask.resize(source.size, Image.Resampling.LANCZOS)
        rgba = source.convert("RGBA")
        rgba.putalpha(mask)
        output = io.BytesIO()
        rgba.save(output, format="PNG", compress_level=6)
        return {
            "mask_bytes_b64": base64.b64encode(output.getvalue()).decode("ascii"),
            "source_size": [source.width, source.height],
            "engine": ENGINE,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        }


@app.function(image=image, timeout=10 * 60)
@modal.asgi_app()
def web():
    from fastapi import Body, FastAPI, HTTPException

    api = FastAPI(title="modal-3D rembg", version="1")

    @api.get("/health")
    def health() -> dict:
        return {"status": "healthy", "service": APP_NAME, "engine": ENGINE}

    @api.post("/preprocess")
    async def preprocess(data: bytes = Body(...)) -> dict:
        if not data:
            raise HTTPException(status_code=400, detail="请求体不能为空")
        if len(data) > 20 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="图片超过 20 MiB")
        worker = RemBgWorker()
        try:
            return worker.process.remote(data)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - surfaced as 500 with type
            raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc

    return api


def deployed() -> bool:
    """Whether the rembg app is reachable for the connected account."""
    try:
        client = modal_client.client()
    except modal_client.NotConnectedError:
        return False
    try:
        modal.Function.from_name(APP_NAME, "web", client=client).hydrate(client=client)
    except Exception:  # noqa: BLE001 - any lookup failure means "not deployed"
        return False
    return True


def deploy() -> dict:
    """Deploy (or redeploy) the rembg app with the connected Modal client.

    Returns the resolved web URL plus a redeploy flag, so the UI can confirm
    the app is live and tell the user whether this created or updated it.
    """
    client = modal_client.require_client()
    already = deployed()
    app.deploy(client=client)
    fn = modal.Function.from_name(APP_NAME, "web", client=client)
    return {
        "ok": True,
        "app": APP_NAME,
        "redeploy": already,
        "web_url": fn.get_web_url(),
    }
