from __future__ import annotations

import argparse
import hmac
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from local_sam_runtime import VERSION

MAX_REQUEST_BYTES = 256 * 1024


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"missing environment variable: {name}")
    return value


def _check() -> int:
    import sam3.model_builder
    import torch
    import torchvision
    from sam3.model.sam3_image_processor import Sam3Processor

    print(
        json.dumps(
            {
                "ok": True,
                "runtime_version": VERSION,
                "python": sys.version.split()[0],
                "torch": torch.__version__,
                "torchvision": torchvision.__version__,
                "sam3_builder": bool(sam3.model_builder.build_sam3_image_model),
                "processor": bool(Sam3Processor),
                "cuda_available": torch.cuda.is_available(),
            }
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        return _check()

    from local_sam_runtime.engine import SamRuntime

    token = _required_env("MODAL_3D_LOCAL_SAM_TOKEN")
    handshake = Path(_required_env("MODAL_3D_LOCAL_SAM_HANDSHAKE"))
    data_dir = Path(_required_env("MODAL_3D_LOCAL_SAM_DATA_DIR"))
    checkpoint = Path(_required_env("MODAL_3D_LOCAL_SAM_CHECKPOINT"))
    projects_dir = Path(_required_env("MODAL_3D_LOCAL_SAM_PROJECTS_DIR")).resolve()
    runtime = SamRuntime(data_dir, checkpoint)

    class Handler(BaseHTTPRequestHandler):
        server_version = "modal-3D-local-sam/1"

        def log_message(self, _format: str, *_args) -> None:
            return

        def _json(self, status: int, value: dict) -> None:
            body = json.dumps(value, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            provided = self.headers.get("X-Modal-3D-Local-SAM", "")
            return hmac.compare_digest(provided, token)

        def _body(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ValueError("invalid content length") from exc
            if length <= 0 or length > MAX_REQUEST_BYTES:
                raise ValueError("invalid request size")
            value = json.loads(self.rfile.read(length))
            if not isinstance(value, dict):
                raise TypeError("JSON body must be an object")
            return value

        def _project_file(self, raw: str) -> Path:
            path = Path(raw).resolve()
            try:
                path.relative_to(projects_dir)
            except ValueError as exc:
                raise ValueError("image_path must be inside the project workspace") from exc
            if not path.is_file():
                raise FileNotFoundError(path)
            return path

        def do_GET(self) -> None:
            if not self._authorized():
                self._json(401, {"detail": "invalid local SAM session"})
                return
            if self.path != "/health":
                self._json(404, {"detail": "not found"})
                return
            self._json(200, runtime.health())

        def do_POST(self) -> None:
            if not self._authorized():
                self._json(401, {"detail": "invalid local SAM session"})
                return
            try:
                body = self._body()
                if self.path == "/segment":
                    value = runtime.segment(
                        self._project_file(str(body["image_path"])),
                        str(body["concept"]),
                        int(body.get("max_candidates", 8)),
                    )
                elif self.path == "/refine":
                    value = runtime.refine(
                        str(body["scene_id"]),
                        str(body["concept"]),
                        list(body["boxes"]),
                        int(body.get("max_candidates", 8)),
                    )
                elif self.path == "/materialize":
                    value = runtime.materialize(
                        str(body["scene_id"]),
                        str(body["selection_id"]),
                        str(body["candidate_id"]),
                        int(body.get("output_size", 1024)),
                    )
                else:
                    self._json(404, {"detail": "not found"})
                    return
            except KeyError as exc:
                self._json(400, {"detail": f"missing field: {exc.args[0]}"})
                return
            except (TypeError, ValueError) as exc:
                self._json(400, {"detail": str(exc)})
                return
            except FileNotFoundError as exc:
                self._json(404, {"detail": str(exc)})
                return
            except Exception as exc:  # noqa: BLE001 - process boundary must return a terminal error.
                self._json(500, {"detail": str(exc) or type(exc).__name__})
                return
            self._json(200, value)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    handshake.parent.mkdir(parents=True, exist_ok=True)
    handshake.write_text(str(server.server_port))
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        handshake.unlink(missing_ok=True)
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
