from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from agent import artifacts as modal3d_artifacts
from agent import generation, rembg_preprocess
from agent.jobs import jobs as modal3d_jobs
from agent.modal_client import NotConnectedError, connected
from agent.models import CapabilityError, public_models

from .contracts import ConnectorError, SAFE_ID, SHA256
from .store import ConnectorStore

MODAL_2D_PROVIDER = "modal-2d"
MODAL_2D_OPERATION = "modal-2d.image.text_to_image.v1"
MODAL_3D_PROVIDER = "modal-3d"
MODAL_3D_OPERATION = "modal-3d.asset.image_to_3d.v1"


@dataclass(frozen=True, slots=True)
class RemoteSubmission:
    remote_job_id: str
    effective_options: dict[str, object]
    model: dict[str, str | None] | None = None


class ProviderAdapter(Protocol):
    id: str
    operation: str

    def descriptor(self) -> dict[str, object]: ...
    def submit(
        self,
        request: dict[str, object],
        store: ConnectorStore,
        owner: str,
        *,
        connector_job_id: str,
    ) -> RemoteSubmission: ...
    def recover_submission(
        self, connector_job_id: str, request: dict[str, object]
    ) -> RemoteSubmission | None: ...
    def poll(self, remote_job_id: str) -> dict[str, object]: ...
    def cancel(self, remote_job_id: str) -> dict[str, object]: ...
    def collect(
        self,
        remote_job_id: str,
        state: dict[str, object],
        store: ConnectorStore,
        owner: str,
        job_id: str,
    ) -> list[dict[str, object]]: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _loopback_origin(value: str) -> str:
    try:
        parsed = urlsplit(str(value).strip())
        port = parsed.port
    except ValueError as exc:
        raise ValueError("provider endpoint must be a valid URL") from exc
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("provider endpoint must be loopback http")
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("provider endpoint must be a bare loopback origin")
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    return f"http://{host}:{port}" if port is not None and port != 80 else f"http://{host}"


class Modal2DAdapter:
    id = MODAL_2D_PROVIDER
    operation = MODAL_2D_OPERATION

    def __init__(self, base_url: str, session_token: str = "", *, timeout: float = 15.0) -> None:
        self.base_url = _loopback_origin(base_url)
        self.session_token = str(session_token or "")
        self.timeout = timeout
        self._opener = build_opener(_NoRedirect)

    def _request(self, method: str, path: str, body: dict[str, object] | None = None):
        headers = {"Accept": "application/json"}
        if self.session_token:
            headers["X-Modal-2D-Session"] = self.session_token
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        request = Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            return self._opener.open(request, timeout=self.timeout)
        except HTTPError as exc:
            if exc.code in {401, 403, 409, 503}:
                raise ConnectorError("CONNECTION_REQUIRED", 503, "modal-2d is not connected", recoverable=True) from exc
            if 400 <= exc.code < 500:
                raise ConnectorError("PROVIDER_REQUEST_INVALID", 422, "modal-2d rejected request") from exc
            raise ConnectorError("PROVIDER_UNAVAILABLE", 503, "modal-2d unavailable", recoverable=True) from exc
        except (URLError, TimeoutError, socket.timeout) as exc:
            raise ConnectorError("CONNECTION_REQUIRED", 503, "modal-2d unavailable", recoverable=True) from exc

    def _json(self, method: str, path: str, body: dict[str, object] | None = None) -> dict[str, object]:
        with self._request(method, path, body) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ConnectorError("PROVIDER_INVALID_RESPONSE", 502, "modal-2d JSON response too large")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ConnectorError("PROVIDER_INVALID_RESPONSE", 502, "modal-2d returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise ConnectorError("PROVIDER_INVALID_RESPONSE", 502, "modal-2d returned invalid object")
        return value

    def descriptor(self) -> dict[str, object]:
        try:
            models = self._json("GET", "/v1/models").get("models")
            model_list = models if isinstance(models, list) else []
            available = bool(model_list)
        except ConnectorError:
            model_list = []
            available = False
        return {
            "id": self.id,
            "displayName": "Modal 2D",
            "version": "1",
            "implementationRevision": "bridge-v1",
            "health": "healthy" if available else "unavailable",
            "status": "available" if available else "disabled",
            "contractVersion": "1",
            "artifactTransport": "connector-artifact",
            "capabilities": [
                {
                    "operation": self.operation,
                    "version": "1",
                    "displayName": "Text to Image",
                    "category": "image-generation",
                    "status": "available" if available else "disabled",
                    "input": {"types": ["text"], "limits": {"maxPromptChars": 4000}},
                    "output": {"roles": ["primary-image"], "required": ["primary-image"], "optional": []},
                    "profiles": {"recommended": {"models": [str(item.get("id")) for item in model_list if isinstance(item, dict) and item.get("id")]}},
                    "optionsSchema": {"type": "object"},
                    "execution": {"async": True, "stages": ["running", "artifact"], "durationClass": "medium", "costClass": "gpu"},
                    "prerequisites": {"authMode": "connector-session", "connection": True},
                    "support": {"cancel": True, "resume": True, "idempotency": True},
                    "artifactTransport": "connector-artifact",
                }
            ],
        }

    def submit(
        self,
        request: dict[str, object],
        store: ConnectorStore,
        owner: str,
        *,
        connector_job_id: str,
    ) -> RemoteSubmission:
        del connector_job_id
        inputs = request["inputs"]
        if not isinstance(inputs, dict):
            raise ConnectorError("INVALID_REQUEST", 422, "modal-2d inputs must be an object")
        body = {
            "prompt": inputs.get("prompt"),
            "model": inputs.get("model"),
            "seed": inputs.get("seed", 42),
        }
        if inputs.get("guidance") is not None:
            body["guidance"] = inputs["guidance"]
        state = self._json("POST", "/v1/jobs", body)
        remote_id = state.get("id")
        if not isinstance(remote_id, str) or not SAFE_ID.fullmatch(remote_id):
            raise ConnectorError("PROVIDER_INVALID_RESPONSE", 502, "modal-2d invalid job identity")
        if state.get("model") != body["model"]:
            raise ConnectorError("PROVIDER_INVALID_RESPONSE", 502, "modal-2d model identity drift")
        return RemoteSubmission(
            remote_job_id=remote_id,
            effective_options={key: value for key, value in body.items() if key != "prompt"},
            model={"id": str(body["model"]), "version": None, "revision": None},
        )

    def recover_submission(
        self, connector_job_id: str, request: dict[str, object]
    ) -> RemoteSubmission | None:
        # 2D provider-local API 目前没有可由 Connector Job ID 反查的 durable identity。
        # 结果未知时必须保持 fail-closed，绝不自动重提。
        del connector_job_id, request
        return None

    def poll(self, remote_job_id: str) -> dict[str, object]:
        if not SAFE_ID.fullmatch(remote_job_id):
            raise ConnectorError("PROVIDER_INVALID_RESPONSE", 502, "modal-2d invalid job identity")
        state = self._json("GET", f"/v1/jobs/{remote_job_id}")
        if state.get("id") != remote_job_id:
            raise ConnectorError("PROVIDER_INVALID_RESPONSE", 502, "modal-2d job identity drift")
        return state

    def cancel(self, remote_job_id: str) -> dict[str, object]:
        if not SAFE_ID.fullmatch(remote_job_id):
            raise ConnectorError("PROVIDER_INVALID_RESPONSE", 502, "modal-2d invalid job identity")
        state = self._json("DELETE", f"/v1/jobs/{remote_job_id}")
        if state.get("id") != remote_job_id:
            raise ConnectorError("PROVIDER_INVALID_RESPONSE", 502, "modal-2d cancel identity drift")
        return state

    def collect(
        self,
        remote_job_id: str,
        state: dict[str, object],
        store: ConnectorStore,
        owner: str,
        job_id: str,
    ) -> list[dict[str, object]]:
        result = state.get("result")
        descriptor = result.get("artifact") if isinstance(result, dict) else None
        if not isinstance(descriptor, dict):
            raise ConnectorError("ARTIFACT_INVALID", 502, "modal-2d result missing artifact")
        artifact_id = descriptor.get("id")
        if not isinstance(artifact_id, str) or not SAFE_ID.fullmatch(artifact_id):
            raise ConnectorError("ARTIFACT_INVALID", 502, "modal-2d artifact identity invalid")
        if (
            descriptor.get("role") != "primary-image"
            or descriptor.get("mime") != "image/png"
            or descriptor.get("format") != "png"
            or descriptor.get("width") != 1024
            or descriptor.get("height") != 1024
        ):
            raise ConnectorError("ARTIFACT_INVALID", 502, "modal-2d artifact contract mismatch")
        digest = descriptor.get("sha256")
        size = descriptor.get("bytes")
        if not isinstance(digest, str) or not SHA256.fullmatch("sha256:" + digest):
            raise ConnectorError("ARTIFACT_INVALID", 502, "modal-2d artifact hash invalid")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0 or size > 64 * 1024 * 1024:
            raise ConnectorError("ARTIFACT_INVALID", 502, "modal-2d artifact length invalid")
        if not SAFE_ID.fullmatch(remote_job_id):
            raise ConnectorError("PROVIDER_INVALID_RESPONSE", 502, "modal-2d invalid job identity")
        with self._request("GET", f"/v1/jobs/{remote_job_id}/artifact") as response:
            content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            if content_type != "image/png":
                raise ConnectorError("ARTIFACT_INVALID", 502, "modal-2d artifact MIME mismatch")
            if response.headers.get("X-Artifact-ID") != artifact_id:
                raise ConnectorError("ARTIFACT_INVALID", 502, "modal-2d artifact identity header mismatch")
            if response.headers.get("X-Artifact-SHA256") != digest:
                raise ConnectorError("ARTIFACT_INVALID", 502, "modal-2d artifact hash header mismatch")
            data = response.read(size + 1)
        return [
            store.import_bytes(
                owner=owner,
                producer_job_id=job_id,
                role="primary-image",
                mime="image/png",
                data=data,
                expected_hash="sha256:" + digest,
                expected_bytes=size,
            )
        ]


class Modal3DAdapter:
    id = MODAL_3D_PROVIDER
    operation = MODAL_3D_OPERATION

    def descriptor(self) -> dict[str, object]:
        try:
            models = public_models()
        except CapabilityError:
            models = []
        enabled = [item for item in models if item.get("status") == "enabled"]
        available = bool(enabled)
        health = "healthy" if available and connected() else "degraded" if available else "unavailable"
        profiles = sorted({str(profile.get("id")) for model in enabled for profile in model.get("profiles", []) if profile.get("id")})
        return {
            "id": self.id,
            "displayName": "Modal 3D",
            "version": "1",
            "implementationRevision": "native-v1",
            "health": health,
            "status": "available" if available else "disabled",
            "contractVersion": "1",
            "artifactTransport": "connector-artifact",
            "capabilities": [
                {
                    "operation": self.operation,
                    "version": "1",
                    "displayName": "Image to 3D",
                    "category": "asset-generation",
                    "status": "available" if available else "disabled",
                    "input": {"types": ["artifact:image/png"]},
                    "output": {"roles": ["primary-glb"], "required": ["primary-glb"], "optional": []},
                    "profiles": {profile: {"label": profile} for profile in profiles},
                    "optionsSchema": {"type": "object"},
                    "execution": {"async": True, "stages": ["preprocess", "running", "artifact"], "durationClass": "long", "costClass": "gpu"},
                    "prerequisites": {"authMode": "connector-session", "connection": True},
                    "support": {"cancel": True, "resume": True, "idempotency": True},
                    "artifactTransport": "connector-artifact",
                }
            ],
        }

    def submit(
        self,
        request: dict[str, object],
        store: ConnectorStore,
        owner: str,
        *,
        connector_job_id: str,
    ) -> RemoteSubmission:
        inputs = request["inputs"]
        if not isinstance(inputs, dict):
            raise ConnectorError("INVALID_REQUEST", 422, "modal-3d inputs must be an object")
        source = inputs.get("sourceArtifact")
        if not isinstance(source, dict):
            raise ConnectorError("INVALID_REQUEST", 422, "modal-3d sourceArtifact is required")
        source_id = str(source.get("id") or "")
        summary, source_path = store.artifact(owner, source_id)
        if summary["role"] != "primary-image" or summary["mime"] != "image/png" or summary["hash"] != source.get("hash"):
            raise ConnectorError("INVALID_REQUEST", 422, "modal-3d source artifact identity mismatch")
        model = str(inputs.get("model") or "")
        seed = inputs.get("seed", 42)
        profile = str(request.get("profile") or "recommended")
        try:
            processed = rembg_preprocess.process(source_path.read_bytes())
            canonical = bytes(processed["canonical_bytes"])
            uploaded = modal3d_artifacts.put(canonical, ".png")
            if uploaded["sha256"] != processed["canonical_sha256"] or uploaded["bytes"] != len(canonical):
                raise ConnectorError("ARTIFACT_INVALID", 502, "canonical upload integrity mismatch")
            remote = generation.submit(model, str(uploaded["path"]), profile, int(seed))
        except ConnectorError:
            raise
        except (NotConnectedError, CapabilityError) as exc:
            raise ConnectorError("CONNECTION_REQUIRED", 503, "modal-3d is not connected", recoverable=True) from exc
        except (ValueError, TypeError) as exc:
            raise ConnectorError("PROVIDER_REQUEST_INVALID", 422, "modal-3d rejected request") from exc
        call_id = str(remote["call_id"])
        try:
            local_job = modal3d_jobs.create(
                str(remote["model"]), call_id, job_id=connector_job_id
            )
        except Exception as exc:
            try:
                generation.cancel_call(call_id)
            except Exception:
                pass
            raise ConnectorError(
                "SUBMISSION_OUTCOME_UNKNOWN",
                503,
                "modal-3d remote submission could not be durably recorded",
                recoverable=True,
            ) from exc
        return RemoteSubmission(
            remote_job_id=str(local_job["id"]),
            effective_options={"model": model, "profile": profile, "seed": int(seed)},
            model={"id": model, "version": None, "revision": None},
        )

    def recover_submission(
        self, connector_job_id: str, request: dict[str, object]
    ) -> RemoteSubmission | None:
        try:
            local_job = modal3d_jobs.get(connector_job_id)
        except KeyError:
            return None
        inputs = request.get("inputs")
        if not isinstance(inputs, dict):
            raise ConnectorError("INVALID_REQUEST", 422, "modal-3d inputs must be an object")
        model = str(inputs.get("model") or "")
        if local_job.get("model") != model:
            raise ConnectorError(
                "PROVIDER_IDENTITY_MISMATCH",
                409,
                "modal-3d local job identity does not match request",
            )
        profile = str(request.get("profile") or "recommended")
        seed = inputs.get("seed", 42)
        return RemoteSubmission(
            remote_job_id=connector_job_id,
            effective_options={"model": model, "profile": profile, "seed": int(seed)},
            model={"id": model, "version": None, "revision": None},
        )

    def poll(self, remote_job_id: str) -> dict[str, object]:
        try:
            return modal3d_jobs.poll(remote_job_id)
        except KeyError as exc:
            raise ConnectorError("REMOTE_JOB_EXPIRED", 410, "modal-3d local job missing") from exc

    def cancel(self, remote_job_id: str) -> dict[str, object]:
        try:
            return modal3d_jobs.cancel(remote_job_id)
        except KeyError as exc:
            raise ConnectorError("REMOTE_JOB_EXPIRED", 410, "modal-3d local job missing") from exc

    def collect(
        self,
        remote_job_id: str,
        state: dict[str, object],
        store: ConnectorStore,
        owner: str,
        job_id: str,
    ) -> list[dict[str, object]]:
        try:
            descriptor, path = modal3d_jobs.artifact(remote_job_id)
        except (KeyError, RuntimeError, FileNotFoundError) as exc:
            raise ConnectorError("ARTIFACT_MISSING", 410, "modal-3d artifact unavailable") from exc
        digest = str(descriptor.get("sha256") or "")
        size = descriptor.get("bytes")
        if not digest or not isinstance(size, int):
            raise ConnectorError("ARTIFACT_INVALID", 502, "modal-3d artifact descriptor invalid")
        return [
            store.import_file(
                owner=owner,
                producer_job_id=job_id,
                role="primary-glb",
                mime="model/gltf-binary",
                path=path,
                expected_hash="sha256:" + digest,
                expected_bytes=size,
            )
        ]


def default_adapters() -> tuple[ProviderAdapter, ...]:
    return (
        Modal2DAdapter(
            os.environ.get("MODAL_CONNECTOR_2D_URL", "http://127.0.0.1:3212"),
            os.environ.get("MODAL_CONNECTOR_2D_SESSION", ""),
        ),
        Modal3DAdapter(),
    )
