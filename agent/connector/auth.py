from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable
from urllib.parse import urlsplit

from .contracts import CLIENT_ID, CONTRACT_VERSION, SCOPES, ConnectorError


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def normalize_origin(value: str) -> str:
    try:
        parsed = urlsplit(str(value or "").strip())
        port = parsed.port
    except ValueError as exc:
        raise ConnectorError("PAIRING_INVALID", 422, "invalid origin") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConnectorError("PAIRING_INVALID", 422, "origin must use http/https")
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    default = 80 if parsed.scheme == "http" else 443
    suffix = f":{port}" if port is not None and port != default else ""
    return f"{parsed.scheme}://{host}{suffix}"


@dataclass(frozen=True, slots=True)
class SessionRecord:
    token_id: str
    token_digest: str
    client_identity: str
    origin: str
    scopes: tuple[str, ...]
    issued_at: datetime
    expires_at: datetime
    capability_snapshot: dict[str, object]

    @property
    def owner(self) -> str:
        return f"{self.client_identity}|{self.origin}"

    def descriptor(self, connector_identity: dict[str, str]) -> dict[str, object]:
        return {
            "connector": dict(connector_identity),
            "contractVersion": CONTRACT_VERSION,
            "clientIdentity": self.client_identity,
            "tokenId": self.token_id,
            "scopes": list(self.scopes),
            "issuedAt": iso(self.issued_at),
            "expiresAt": iso(self.expires_at),
            "allowedOrigins": [self.origin],
            "capabilityRevision": self.capability_snapshot["revision"],
            "capabilityHash": self.capability_snapshot["hash"],
            "revokeEndpoint": "/connector/v1/session",
        }


class SessionStore:
    def __init__(
        self,
        *,
        connector_identity: dict[str, str],
        pairing_secret: str,
        allowed_origins: tuple[str, ...],
        snapshot_factory: Callable[[datetime], dict[str, object]],
        ttl: timedelta = timedelta(minutes=30),
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self.connector_identity = dict(connector_identity)
        self._pairing_secret = str(pairing_secret or "")
        self._allowed_origins = frozenset(normalize_origin(item) for item in allowed_origins)
        self._snapshot_factory = snapshot_factory
        self._ttl = ttl
        self._now = now
        self._records: dict[str, SessionRecord] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def pair(
        self,
        *,
        approval: str,
        client_identity: str,
        contract_version: str,
        origin: str,
        scopes: list[str],
    ) -> dict[str, object]:
        if not self._pairing_secret or not hmac.compare_digest(str(approval or ""), self._pairing_secret):
            raise ConnectorError("PAIRING_REQUIRED", 401, "desktop pairing approval required")
        if client_identity != CLIENT_ID or contract_version != CONTRACT_VERSION:
            raise ConnectorError("PAIRING_INCOMPATIBLE", 409, "client or contract version mismatch")
        normalized_origin = normalize_origin(origin)
        if normalized_origin not in self._allowed_origins:
            raise ConnectorError("PAIRING_ORIGIN_DENIED", 403, "origin is not approved")
        granted = tuple(dict.fromkeys(str(scope).strip() for scope in scopes if str(scope).strip()))
        if not granted or any(scope not in SCOPES for scope in granted):
            raise ConnectorError("PAIRING_SCOPE_DENIED", 403, "invalid requested scope")

        issued = self._now().astimezone(UTC)
        expires = issued + self._ttl
        snapshot = self._snapshot_factory(expires)
        token = secrets.token_urlsafe(32)
        record = SessionRecord(
            token_id=f"tok_{secrets.token_hex(12)}",
            token_digest=self._digest(token),
            client_identity=CLIENT_ID,
            origin=normalized_origin,
            scopes=granted,
            issued_at=issued,
            expires_at=expires,
            capability_snapshot=snapshot,
        )
        with self._lock:
            self._records[record.token_digest] = record
        return {"token": token, "session": record.descriptor(self.connector_identity)}

    def authenticate(self, authorization: str, origin: str, scope: str) -> SessionRecord:
        if not authorization.startswith("Bearer "):
            raise ConnectorError("CONNECTION_REQUIRED", 401, "missing connector session")
        token = authorization[7:].strip()
        if not token:
            raise ConnectorError("CONNECTION_REQUIRED", 401, "missing connector session")
        with self._lock:
            record = self._records.get(self._digest(token))
        if record is None or record.expires_at <= self._now().astimezone(UTC):
            raise ConnectorError("CONNECTION_REQUIRED", 401, "connector session unavailable")
        if normalize_origin(origin) != record.origin:
            raise ConnectorError("CONNECTION_REQUIRED", 403, "connector session origin mismatch")
        if scope not in record.scopes:
            raise ConnectorError("SCOPE_DENIED", 403, "connector session scope denied")
        return record

    def revoke(self, authorization: str, origin: str) -> None:
        if not authorization.startswith("Bearer "):
            raise ConnectorError("CONNECTION_REQUIRED", 401, "missing connector session")
        token = authorization[7:].strip()
        digest = self._digest(token)
        with self._lock:
            record = self._records.get(digest)
        if record is None or record.expires_at <= self._now().astimezone(UTC):
            raise ConnectorError("CONNECTION_REQUIRED", 401, "connector session unavailable")
        if normalize_origin(origin) != record.origin:
            raise ConnectorError("CONNECTION_REQUIRED", 403, "connector session origin mismatch")
        with self._lock:
            self._records.pop(digest, None)
