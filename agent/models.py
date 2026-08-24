from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import modal
from modal.exception import (
    AuthError,
    InternalError,
    PermissionDeniedError,
    ResourceExhaustedError,
    ServiceError,
)
from modal.exception import ConnectionError as ModalConnectionError
from modal.exception import TimeoutError as ModalTimeoutError

from agent.modal_client import NotConnectedError, client, connected
from agent.storage import data_dir

APP_NAME = "modal-3d-gateway"
CONTRACT = "modal-3d.capabilities.v1"
_CACHE_NAME = "generation-capabilities.json"
_RECOVERABLE_ERRORS = (
    NotConnectedError,
    AuthError,
    PermissionDeniedError,
    ModalConnectionError,
    InternalError,
    ServiceError,
    ResourceExhaustedError,
    ModalTimeoutError,
    TimeoutError,
)
_lock = threading.RLock()


class CapabilityError(RuntimeError):
    pass


class CapabilityUnavailable(CapabilityError):
    pass


class IncompatibleCapability(CapabilityError):
    pass


def _cache_path() -> Path:
    return data_dir() / _CACHE_NAME


def _require_mapping(value, name: str) -> dict:
    if not isinstance(value, dict):
        raise IncompatibleCapability(f"{name} must be an object")
    return value


def _require_string(value, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise IncompatibleCapability(f"{name} must be a non-empty string")
    return value


def _validate_option_value(name: str, value, schema: dict) -> None:
    if value is None:
        if schema.get("nullable") is True:
            return
        raise IncompatibleCapability(f"option {name} must not be null")
    kind = schema.get("type")
    if kind == "integer":
        valid = isinstance(value, int) and not isinstance(value, bool)
    elif kind == "number":
        valid = isinstance(value, (int, float)) and not isinstance(value, bool)
    else:
        raise IncompatibleCapability(f"unsupported option type for {name}: {kind}")
    if not valid:
        raise IncompatibleCapability(f"option {name} must be {kind}")
    minimum = schema.get("minimum")
    if minimum is not None and value < minimum:
        raise IncompatibleCapability(f"option {name} must be >= {minimum}")
    maximum = schema.get("maximum")
    if maximum is not None and value > maximum:
        raise IncompatibleCapability(f"option {name} must be <= {maximum}")


def _validate_document(value) -> dict:
    document = _require_mapping(value, "capabilities")
    if document.get("contract") != CONTRACT:
        raise IncompatibleCapability(
            f"incompatible generation capability contract: {document.get('contract')!r}"
        )

    generation = _require_mapping(document.get("generation"), "generation")
    if generation.get("app") != APP_NAME or generation.get("submit_function") != "submit":
        raise IncompatibleCapability("generation endpoint does not match the supported gateway")

    models = document.get("models")
    if not isinstance(models, list) or not models:
        raise IncompatibleCapability("models must be a non-empty array")

    seen_models: set[str] = set()
    for index, raw_model in enumerate(models):
        model = _require_mapping(raw_model, f"models[{index}]")
        model_id = _require_string(model.get("id"), f"models[{index}].id")
        if model_id in seen_models:
            raise IncompatibleCapability(f"duplicate model id: {model_id}")
        seen_models.add(model_id)
        _require_string(model.get("name"), f"models[{index}].name")
        if not isinstance(model.get("description"), str):
            raise IncompatibleCapability(f"models[{index}].description must be a string")
        if model.get("status") not in {"enabled", "degraded", "disabled"}:
            raise IncompatibleCapability(f"models[{index}].status is invalid")
        if model.get("output") not in {"geometry", "textured"}:
            raise IncompatibleCapability(f"models[{index}].output is invalid")

        reference = _require_mapping(model.get("reference"), f"models[{index}].reference")
        warm_seconds = reference.get("warm_seconds")
        if not isinstance(warm_seconds, (int, float)) or isinstance(warm_seconds, bool):
            raise IncompatibleCapability(f"models[{index}].reference.warm_seconds must be number")

        option_schemas = _require_mapping(model.get("options"), f"models[{index}].options")
        profiles = model.get("profiles")
        if not isinstance(profiles, list) or not profiles:
            raise IncompatibleCapability(f"models[{index}].profiles must be a non-empty array")
        seen_profiles: set[str] = set()
        for profile_index, raw_profile in enumerate(profiles):
            profile = _require_mapping(raw_profile, f"models[{index}].profiles[{profile_index}]")
            profile_id = _require_string(
                profile.get("id"), f"models[{index}].profiles[{profile_index}].id"
            )
            if profile_id in seen_profiles:
                raise IncompatibleCapability(f"duplicate profile id for {model_id}: {profile_id}")
            seen_profiles.add(profile_id)
            _require_string(
                profile.get("name"), f"models[{index}].profiles[{profile_index}].name"
            )
            options = _require_mapping(
                profile.get("options"), f"models[{index}].profiles[{profile_index}].options"
            )
            unknown = sorted(set(options) - set(option_schemas))
            if unknown:
                raise IncompatibleCapability(
                    f"profile {model_id}/{profile_id} references unknown options: {', '.join(unknown)}"
                )
            for name, option_value in options.items():
                schema = _require_mapping(option_schemas[name], f"option schema {model_id}.{name}")
                _validate_option_value(name, option_value, schema)

        seed_schema = option_schemas.get("seed")
        if not isinstance(seed_schema, dict) or seed_schema.get("type") != "integer":
            raise IncompatibleCapability(f"model {model_id} must define integer seed option")

    return document


def _write_cache(document: dict) -> None:
    path = _cache_path()
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _read_cache() -> dict | None:
    path = _cache_path()
    if not path.is_file():
        return None
    try:
        return _validate_document(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, IncompatibleCapability):
        return None


def refresh_capabilities() -> dict:
    fn = modal.Function.from_name(APP_NAME, "capabilities", client=client())
    document = _validate_document(fn.remote())
    with _lock:
        _write_cache(document)
    return document


def capabilities_document(*, refresh: bool = True) -> dict:
    if refresh and connected():
        try:
            return refresh_capabilities()
        except IncompatibleCapability:
            raise
        except _RECOVERABLE_ERRORS:
            pass

    with _lock:
        cached = _read_cache()
    if cached is not None:
        return cached
    if not connected():
        raise CapabilityUnavailable("模型 capability 尚不可用；请先连接 Modal")
    raise CapabilityUnavailable("无法读取云端模型 capability，且没有可用缓存")


def public_models() -> list[dict]:
    document = capabilities_document()
    return [
        {
            "id": model["id"],
            "name": model["name"],
            "description": model["description"],
            "status": model["status"],
            "output": model["output"],
            "warm_seconds": float(model["reference"]["warm_seconds"]),
            "profiles": [
                {"id": profile["id"], "name": profile["name"]} for profile in model["profiles"]
            ],
        }
        for model in document["models"]
    ]


def options_for(model_id: str, profile_id: str, seed: int) -> dict:
    document = capabilities_document()
    model = next((item for item in document["models"] if item["id"] == model_id), None)
    if model is None:
        raise ValueError(f"未知模型：{model_id}")
    if model["status"] != "enabled":
        raise ValueError(f"模型 {model_id} 当前不可用：{model['status']}")
    profile = next((item for item in model["profiles"] if item["id"] == profile_id), None)
    if profile is None:
        raise ValueError(f"模型 {model_id} 不支持 profile：{profile_id}")
    _validate_option_value("seed", seed, model["options"]["seed"])
    return {"seed": seed, **profile["options"]}
