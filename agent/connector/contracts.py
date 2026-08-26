from __future__ import annotations

import json
import math
import re
from decimal import Decimal
from hashlib import sha256
from typing import Any

CONTRACT_VERSION = "1"
CLIENT_ID = "agentscape"
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,160}$")
SAFE_KEY = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
SECRET_KEY = re.compile(
    r"authorization|api[-_]?key|access[-_]?token|refresh[-_]?token|secret|credential|signed[-_]?url",
    re.IGNORECASE,
)
JS_MAX_SAFE_INTEGER = 2**53 - 1
SCOPES = frozenset({
    "capabilities.read", "jobs.submit", "jobs.read", "jobs.cancel", "artifacts.read"
})
TERMINAL = frozenset({"succeeded", "failed", "cancelled", "expired"})


class ConnectorError(RuntimeError):
    def __init__(self, code: str, status: int, message: str, *, recoverable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.recoverable = recoverable


def require_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ConnectorError("INVALID_REQUEST", 422, f"missing {field}")
    return text


def safe_json(value: Any, path: str = "value") -> Any:
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        if abs(value) > JS_MAX_SAFE_INTEGER:
            raise ConnectorError("INVALID_REQUEST", 422, f"unsafe integer at {path}")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ConnectorError("INVALID_REQUEST", 422, f"non-finite number at {path}")
        return value
    if isinstance(value, list):
        return [safe_json(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ConnectorError("INVALID_REQUEST", 422, f"non-string key at {path}")
            if SECRET_KEY.search(key):
                raise ConnectorError("INVALID_REQUEST", 422, f"secret field at {path}.{key}")
            result[key] = safe_json(item, f"{path}.{key}")
        return result
    raise ConnectorError("INVALID_REQUEST", 422, f"non-json value at {path}")


def _utf16_key(value: str) -> bytes:
    return value.encode("utf-16-be", "surrogatepass")


def _array_index(value: str) -> int | None:
    if not value or not value.isascii() or not value.isdigit():
        return None
    if value != "0" and value.startswith("0"):
        return None
    index = int(value)
    return index if index < 2**32 - 1 else None


def _js_keys(value: dict[str, Any]) -> list[str]:
    ordered = sorted(value, key=_utf16_key)
    indices = sorted((key for key in ordered if _array_index(key) is not None), key=int)
    return indices + [key for key in ordered if _array_index(key) is None]


def _js_number(value: int | float) -> str:
    if isinstance(value, int):
        return str(value)
    if value == 0:
        return "0"
    if value.is_integer() and abs(value) < 1e21:
        return str(int(value))
    text = repr(value).lower()
    absolute = abs(value)
    if 1e-6 <= absolute < 1e21:
        return format(Decimal(text), "f") if "e" in text else text
    if "e" not in text:
        text = format(value, ".17e")
    mantissa, exponent = text.split("e", 1)
    if mantissa.endswith(".0"):
        mantissa = mantissa[:-2]
    exponent_value = int(exponent)
    sign = "+" if exponent_value >= 0 else "-"
    return f"{mantissa}e{sign}{abs(exponent_value)}"


def _js_string(value: str) -> str:
    dumped = json.dumps(value, ensure_ascii=False)
    return "".join(
        f"\\u{ord(char):04x}" if 0xD800 <= ord(char) <= 0xDFFF else char
        for char in dumped
    )


def _js_json(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return _js_number(value)
    if isinstance(value, str):
        return _js_string(value)
    if isinstance(value, list):
        return "[" + ",".join(_js_json(item) for item in value) + "]"
    return "{" + ",".join(f"{_js_string(key)}:{_js_json(value[key])}" for key in _js_keys(value)) + "}"


def canonical_request(envelope: dict[str, Any]) -> dict[str, Any]:
    roles_raw = envelope.get("outputRoles", [])
    if not isinstance(roles_raw, list):
        raise ConnectorError("INVALID_REQUEST", 422, "outputRoles must be an array")
    roles = sorted({require_text(item, "outputRoles[]") for item in roles_raw}, key=_utf16_key)
    canonical = {
        "provider": require_text(envelope.get("provider"), "provider"),
        "operation": require_text(envelope.get("operation"), "operation"),
        "inputs": safe_json(envelope.get("inputs", {}), "inputs"),
        "profile": None if envelope.get("profile") is None else str(envelope.get("profile")),
        "options": safe_json(envelope.get("options", {}), "options"),
        "outputRoles": roles,
        "parent": None if envelope.get("parent") is None else safe_json(envelope.get("parent"), "parent"),
        "retention": None if envelope.get("retention") is None else safe_json(envelope.get("retention"), "retention"),
        "metadata": None if envelope.get("metadata") is None else safe_json(envelope.get("metadata"), "metadata"),
    }
    if not isinstance(canonical["inputs"], dict) or not isinstance(canonical["options"], dict):
        raise ConnectorError("INVALID_REQUEST", 422, "inputs/options must be objects")
    return canonical


def request_hash(envelope: dict[str, Any]) -> str:
    canonical = canonical_request(envelope)
    return "sha256:" + sha256(_js_json(canonical).encode("utf-8")).hexdigest()


def validate_submit(envelope: Any) -> dict[str, Any]:
    if not isinstance(envelope, dict):
        raise ConnectorError("INVALID_REQUEST", 422, "job request must be an object")
    canonical = canonical_request(envelope)
    claimed_hash = require_text(envelope.get("requestHash"), "requestHash")
    if not SHA256.fullmatch(claimed_hash) or request_hash(envelope) != claimed_hash:
        raise ConnectorError("REQUEST_HASH_MISMATCH", 422, "requestHash does not match canonical payload")
    idempotency_key = require_text(envelope.get("idempotencyKey"), "idempotencyKey")
    if not SAFE_KEY.fullmatch(idempotency_key):
        raise ConnectorError("INVALID_REQUEST", 422, "invalid idempotencyKey")
    return {
        **canonical,
        "requestHash": claimed_hash,
        "idempotencyKey": idempotency_key,
        "operationVersion": require_text(envelope.get("operationVersion"), "operationVersion"),
        "contractVersion": require_text(envelope.get("contractVersion"), "contractVersion"),
        "capabilityHash": require_text(envelope.get("capabilityHash"), "capabilityHash"),
        "capabilityRevision": require_text(envelope.get("capabilityRevision"), "capabilityRevision"),
    }
