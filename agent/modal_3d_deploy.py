"""Deploy the complete modal-3D cloud suite from the desktop app.

The desktop client keeps a single source of truth for the 3D service: it fetches
an immutable, SHA-256 verified snapshot of the canonical modal-3D repository,
loads its existing worker/gateway definitions, deploys each worker, registers
its capability, then deploys and verifies the gateway.  Users never need to
clone the server repository or run Modal CLI commands manually.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import shutil
import sys
import threading
import urllib.request
import zipfile
from pathlib import Path
from types import ModuleType

import modal

from agent import modal_client
from agent.storage import data_dir

SOURCE_COMMIT = "ff48ef3e5185cea446af5b152062683c51f1ffee"
SOURCE_SHA256 = "3f4e93998a9491b93b0261fd11d3b2f2ffca7719ab2dd7560b7663738310aa45"
SOURCE_URL = f"https://codeload.github.com/xiaoqianran/modal-3D/zip/{SOURCE_COMMIT}"
GATEWAY_APP = "modal-3d-gateway"
REGISTRY_NAME = "modal-3d-model-registry"
WORKERS = (
    ("fastsam3d_plus_plus", "modal-3d-fastsam3d"),
    ("hunyuan2_1_plus_plus", "modal-3d-hunyuan"),
    ("hermit_trellis2_plus_plus", "modal-3d-hermit-trellis2-plus-plus"),
    ("pixal3d", "modal-3d-pixal3d"),
)

_lock = threading.Lock()
_state_lock = threading.Lock()
_state: dict = {
    "running": False,
    "step": None,
    "component": None,
    "completed_apps": [],
    "error": None,
}


def _set_state(**values) -> None:
    with _state_lock:
        _state.update(values)


def _mark_completed(app_name: str) -> None:
    with _state_lock:
        completed = list(_state.get("completed_apps", []))
        if app_name not in completed:
            completed.append(app_name)
        _state["completed_apps"] = completed


def _source_parent() -> Path:
    return data_dir() / "cloud-3d-source"


def source_root() -> Path:
    return _source_parent() / SOURCE_COMMIT


def _source_marker() -> Path:
    return source_root() / ".modal-3d-source.json"


def _source_ready() -> bool:
    marker = _source_marker()
    package = source_root() / "modal_3d" / "__init__.py"
    if not marker.is_file() or not package.is_file():
        return False
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return value.get("commit") == SOURCE_COMMIT and value.get("sha256") == SOURCE_SHA256


def _download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "modal-3D-client/0.4 deploy"})
    digest = hashlib.sha256()
    temporary = destination.with_suffix(destination.suffix + ".part")
    with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
            digest.update(chunk)
    if digest.hexdigest().lower() != SOURCE_SHA256:
        temporary.unlink(missing_ok=True)
        raise RuntimeError("3D 部署源码 SHA-256 校验失败")
    temporary.replace(destination)


def _safe_extract(archive: Path, destination: Path) -> Path:
    with zipfile.ZipFile(archive) as package:
        infos = package.infolist()
        if not infos:
            raise RuntimeError("3D 部署源码压缩包为空")
        for info in infos:
            parts = Path(info.filename.replace("\\", "/")).parts
            if not parts or Path(info.filename).is_absolute() or ".." in parts:
                raise RuntimeError("3D 部署源码包含不安全路径")
        top_levels = {Path(info.filename).parts[0] for info in infos if Path(info.filename).parts}
        if len(top_levels) != 1:
            raise RuntimeError("3D 部署源码目录结构异常")
        package.extractall(destination)
        return destination / next(iter(top_levels))


def ensure_source() -> Path:
    if _source_ready():
        return source_root()
    parent = _source_parent()
    parent.mkdir(parents=True, exist_ok=True)
    staging = parent / f"{SOURCE_COMMIT}.installing"
    archive = parent / f"{SOURCE_COMMIT}.zip"
    shutil.rmtree(staging, ignore_errors=True)
    archive.unlink(missing_ok=True)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        _set_state(step="source-download", component=None)
        _download(SOURCE_URL, archive)
        _set_state(step="source-verify", component=None)
        extracted = _safe_extract(archive, staging)
        target = source_root()
        shutil.rmtree(target, ignore_errors=True)
        extracted.replace(target)
        (target / ".modal-3d-source.json").write_text(
            json.dumps({"commit": SOURCE_COMMIT, "sha256": SOURCE_SHA256}, indent=2),
            encoding="utf-8",
        )
        return target
    finally:
        archive.unlink(missing_ok=True)
        shutil.rmtree(staging, ignore_errors=True)


def _load_module(name: str) -> ModuleType:
    root = ensure_source()
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    return importlib.import_module(f"modal_3d.{name}")


def _function_exists(app_name: str, function_name: str, client: modal.Client) -> bool:
    try:
        modal.Function.from_name(app_name, function_name, client=client).hydrate(client=client)
    except Exception:  # noqa: BLE001 - lookup failures are intentionally summarized as unavailable.
        return False
    return True


def _component_template() -> list[dict]:
    components = [
        {"kind": "worker", "module": module, "app": app_name, "function": "register"}
        for module, app_name in WORKERS
    ]
    components.append(
        {"kind": "gateway", "module": "gateway", "app": GATEWAY_APP, "function": "capabilities"}
    )
    return components


def _registered_worker_apps(client: modal.Client) -> set[str]:
    try:
        registry = modal.Dict.from_name(REGISTRY_NAME, client=client)
        registry.hydrate(client=client)
        values = [value for _key, value in registry.items()]
    except Exception:  # noqa: BLE001 - missing/unauthorized registry means no registrations.
        return set()
    apps: set[str] = set()
    for value in values:
        if not isinstance(value, dict):
            continue
        worker_app = value.get("worker_app")
        if not worker_app and isinstance(value.get("registration"), dict):
            worker_app = value["registration"].get("worker_app")
        if isinstance(worker_app, str) and worker_app:
            apps.add(worker_app)
    return apps


def _component_status(client: modal.Client) -> list[dict]:
    registered_apps = _registered_worker_apps(client)
    result: list[dict] = []
    for component in _component_template():
        function_exists = _function_exists(component["app"], component["function"], client)
        registered = component["kind"] == "gateway" or component["app"] in registered_apps
        result.append({**component, "deployed": function_exists and registered, "registered": registered})
    return result


def status() -> dict:
    with _state_lock:
        live = dict(_state)
        live["completed_apps"] = list(_state.get("completed_apps", []))

    if live.get("running"):
        completed = set(live.get("completed_apps", []))
        components = [
            {
                **component,
                "deployed": component["app"] in completed,
                "registered": component["kind"] == "gateway" or component["app"] in completed,
            }
            for component in _component_template()
        ]
    else:
        try:
            client = modal_client.client()
        except modal_client.NotConnectedError:
            components = [
                {**component, "deployed": False, "registered": False}
                for component in _component_template()
            ]
        else:
            components = _component_status(client)

    deployed = bool(components) and all(item["deployed"] for item in components)
    return {
        **live,
        "deployed": deployed,
        "source_ready": _source_ready(),
        "source_commit": SOURCE_COMMIT,
        "components": components,
    }


def _preflight(client: modal.Client) -> None:
    # All four canonical workers reference this named secret. Hydration provides a
    # deterministic failure before we spend time building images. The secret can
    # contain HF_TOKEN for gated models; public-model accounts may still use an
    # empty/no-op token value created in Modal.
    try:
        modal.Secret.from_name("huggingface", client=client).hydrate(client=client)
    except Exception as exc:  # noqa: BLE001 - normalize SDK-specific not-found errors for the API.
        raise RuntimeError("Modal 账户缺少名为 huggingface 的 Secret") from exc


def deploy() -> dict:
    if not _lock.acquire(blocking=False):
        raise RuntimeError("3D 模型套件正在部署，请等待当前部署完成")
    try:
        client = modal_client.client()
        _set_state(running=True, step="preflight", component=None, completed_apps=[], error=None)
        _preflight(client)
        ensure_source()
        results: list[dict] = []
        for module_name, app_name in WORKERS:
            _set_state(step="deploy-worker", component=app_name)
            module = _load_module(module_name)
            module.app.deploy(client=client)
            _set_state(step="register-worker", component=app_name)
            registration = modal.Function.from_name(app_name, "register", client=client).remote()
            results.append({"app": app_name, "registered": registration})
            _mark_completed(app_name)

        _set_state(step="deploy-gateway", component=GATEWAY_APP)
        gateway = _load_module("gateway")
        gateway.app.deploy(client=client)
        _mark_completed(GATEWAY_APP)

        _set_state(step="verify", component=GATEWAY_APP)
        capabilities = modal.Function.from_name(GATEWAY_APP, "capabilities", client=client).remote()
        model_ids = [item.get("id") for item in capabilities.get("models", []) if isinstance(item, dict)]
        if len(model_ids) < len(WORKERS):
            raise RuntimeError(f"Gateway 仅发现 {len(model_ids)}/{len(WORKERS)} 个 3D Worker")
        _set_state(running=False, step="ready", component=None, error=None)
        return {
            "ok": True,
            "deployed": True,
            "source_commit": SOURCE_COMMIT,
            "workers": results,
            "gateway": GATEWAY_APP,
            "models": model_ids,
        }
    except Exception as exc:
        _set_state(running=False, step="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        _lock.release()
