"""Deep HTTP boundary around the two reference sidecars.

The hub deliberately does not mirror either provider's capability schema.  It
forwards provider-owned input options and only reads the stable execution and
artifact fields needed by the human workflow.
"""

from __future__ import annotations

import copy
import json
import secrets
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


class SidecarError(RuntimeError):
    def __init__(self, provider: str, message: str, *, status: int = 502) -> None:
        super().__init__(message)
        self.provider = provider
        self.status = status


def project_job(job: dict[str, Any]) -> dict[str, Any]:
    """Project only the stable Sidecar execution contract for caller-owned state."""
    state = str(job.get("status", "uncertain"))
    projection: dict[str, Any] = {
        "state": state,
        "failure": job.get("error_code"),
        "retryable": job.get("retryable"),
    }
    result = job.get("result")
    if isinstance(result, dict):
        artifact = result.get("artifact")
        if isinstance(artifact, dict):
            projection["artifact"] = copy.deepcopy(artifact)
        conditioning = result.get("conditioning")
        if isinstance(conditioning, dict):
            projection["conditioning"] = copy.deepcopy(conditioning)
    if state == "succeeded" and "artifact" not in projection:
        projection.update(
            {"state": "failed", "failure": "provider.missing_artifact", "retryable": False}
        )
    return projection


@dataclass(frozen=True, slots=True)
class SidecarConfig:
    provider: str
    base_url: str
    session_header: str
    session_token: str | None = None


def _multipart(fields: dict[str, object], filename: str, data: bytes) -> tuple[bytes, str]:
    """Build the one-file multipart body expected by modal-3D-client."""
    boundary = f"modal-hub-{secrets.token_hex(16)}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                str(value).encode(),
                b"\r\n",
            ]
        )
    chunks.extend(
        [
            f"--{boundary}\r\n".encode(),
            (f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n').encode(),
            b"Content-Type: application/octet-stream\r\n\r\n",
            data,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


class SidecarClient:
    """Small imperative shell; provider details stay behind this interface."""

    def __init__(self, config: SidecarConfig, *, timeout: float = 15.0) -> None:
        self.config = config
        self.timeout = timeout

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        content_type: str | None = None,
        timeout: float | None = None,
    ) -> tuple[bytes, dict[str, str]]:
        headers = {"Accept": "application/json"}
        if self.config.session_token:
            headers[self.config.session_header] = self.config.session_token
        if content_type:
            headers["Content-Type"] = content_type
        request = Request(
            f"{self.config.base_url.rstrip('/')}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=timeout or self.timeout) as response:
                return response.read(), {
                    key.lower(): value for key, value in response.headers.items()
                }
        except HTTPError as exc:
            detail = exc.read(2048).decode("utf-8", errors="replace")
            raise SidecarError(
                self.config.provider,
                f"{self.config.provider} returned HTTP {exc.code}: {detail}",
                status=exc.code,
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise SidecarError(
                self.config.provider,
                f"{self.config.provider} is unreachable: {exc}",
                status=503,
            ) from exc

    def _json(self, method: str, path: str, payload: object | None = None) -> dict[str, Any]:
        body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        raw, _ = self._request(
            method,
            path,
            body=body,
            content_type="application/json" if body is not None else None,
        )
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SidecarError(self.config.provider, "sidecar returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise SidecarError(self.config.provider, "sidecar response must be an object")
        return value

    def health(self) -> dict[str, Any]:
        return self._json("GET", "/health")

    def connect(self, token_id: str, token_secret: str) -> dict[str, Any]:
        return self._json(
            "POST",
            "/modal/connect",
            {"token_id": token_id, "token_secret": token_secret},
        )

    def disconnect(self) -> dict[str, Any]:
        return self._json("DELETE", "/modal/connect")

    def models(self) -> list[dict[str, Any]]:
        value = self._json("GET", "/v1/models").get("models", [])
        return (
            [dict(item) for item in value if isinstance(item, dict)]
            if isinstance(value, list)
            else []
        )

    def submit_image(self, intent: dict[str, Any], *, job_id: str) -> dict[str, Any]:
        return self._json("POST", "/v1/jobs", {**intent, "job_id": job_id})

    def submit_asset3d(
        self,
        source: bytes,
        *,
        model: str,
        profile: str,
        seed: int,
        job_id: str,
    ) -> dict[str, Any]:
        body, content_type = _multipart(
            {"model": model, "profile": profile, "seed": seed, "job_id": job_id},
            "selected-image",
            source,
        )
        raw, _ = self._request("POST", "/v1/jobs", body=body, content_type=content_type, timeout=60)
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SidecarError(self.config.provider, "sidecar returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise SidecarError(self.config.provider, "sidecar response must be an object")
        return value

    def job(self, job_id: str) -> dict[str, Any]:
        return self._json("GET", f"/v1/jobs/{quote(job_id, safe='')}")

    def cancel(self, job_id: str) -> dict[str, Any]:
        return self._json("DELETE", f"/v1/jobs/{quote(job_id, safe='')}")

    def artifact(self, job_id: str) -> tuple[bytes, dict[str, str]]:
        return self._request("GET", f"/v1/jobs/{quote(job_id, safe='')}/artifact", timeout=60)

    def stream_artifact(self, job_id: str) -> Iterator[bytes]:
        """Stream a potentially large GLB without making the Hub an artifact cache."""
        headers: dict[str, str] = {}
        if self.config.session_token:
            headers[self.config.session_header] = self.config.session_token
        request = Request(
            f"{self.config.base_url.rstrip('/')}/v1/jobs/{quote(job_id, safe='')}/artifact",
            headers=headers,
            method="GET",
        )
        try:
            with urlopen(request, timeout=60) as response:
                while chunk := response.read(1024 * 1024):
                    yield chunk
        except HTTPError as exc:
            raise SidecarError(
                self.config.provider,
                f"{self.config.provider} artifact returned HTTP {exc.code}",
                status=exc.code,
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise SidecarError(
                self.config.provider,
                f"{self.config.provider} artifact stream failed: {exc}",
                status=503,
            ) from exc
