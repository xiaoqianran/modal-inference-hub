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
import os
import shutil
import sys
import threading
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from types import ModuleType

import modal

from agent import modal_client
from agent.storage import data_dir

SOURCE_COMMIT = "c51b9c24d35aab35cc63d1dbaf3f6fe8bdb4e901"
SOURCE_SHA256 = "638fd82dfe30187a05d4fffe5a238f8bb5eabfd1a54f4f40c46cf9a0451974f3"
SOURCE_URL = f"https://codeload.github.com/xiaoqianran/modal-3D/zip/{SOURCE_COMMIT}"
GATEWAY_APP = "modal-3d-gateway"
REGISTRY_NAME = "modal-3d-model-registry"
EXPECTED_WORKER_ADAPTER_REVISION = "modal-3d.worker-adapter.v3"
WORKERS = (
    ("fastsam3d_plus_plus", "modal-3d-fastsam3d"),
    ("hunyuan2_1_plus_plus", "modal-3d-hunyuan"),
    ("hermit_trellis2_plus_plus", "modal-3d-hermit-trellis2-plus-plus"),
    ("pixal3d", "modal-3d-pixal3d"),
)
DEFAULT_WORKER_CONCURRENCY = 3

_lock = threading.Lock()
_state_lock = threading.Lock()
_state: dict = {
    "running": False,
    "step": None,
    "component": None,
    "completed_apps": [],
    "skipped_apps": [],
    "component_errors": {},
    "component_states": {},
    "error": None,
}


def _set_state(**values) -> None:
    with _state_lock:
        _state.update(values)


def _log(message: str) -> None:
    print(f"[modal-3d-deploy] {message}", flush=True)


def _mark_completed(app_name: str, *, skipped: bool = False) -> None:
    with _state_lock:
        completed = list(_state.get("completed_apps", []))
        if app_name not in completed:
            completed.append(app_name)
        _state["completed_apps"] = completed
        if skipped:
            skipped_apps = list(_state.get("skipped_apps", []))
            if app_name not in skipped_apps:
                skipped_apps.append(app_name)
            _state["skipped_apps"] = skipped_apps


def _record_component_error(app_name: str, exc: Exception) -> None:
    with _state_lock:
        errors = dict(_state.get("component_errors", {}))
        errors[app_name] = f"{type(exc).__name__}: {exc}"
        _state["component_errors"] = errors


def _set_component_state(app_name: str, state: str) -> None:
    with _state_lock:
        states = dict(_state.get("component_states", {}))
        states[app_name] = state
        _state["component_states"] = states


def _worker_concurrency() -> int:
    raw = os.environ.get("MODAL_3D_DEPLOY_CONCURRENCY", str(DEFAULT_WORKER_CONCURRENCY))
    try:
        requested = int(raw)
    except ValueError:
        requested = DEFAULT_WORKER_CONCURRENCY
    return max(1, min(len(WORKERS), requested))


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
    request = urllib.request.Request(url, headers={"User-Agent": "modal-inference-hub/0.4 deploy"})
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


def _registered_worker_records(client: modal.Client) -> dict[str, dict]:
    try:
        registry = modal.Dict.from_name(REGISTRY_NAME, client=client)
        registry.hydrate(client=client)
        values = [value for _key, value in registry.items()]
    except Exception:  # noqa: BLE001 - missing/unauthorized registry means no registrations.
        return {}
    records: dict[str, dict] = {}
    for value in values:
        if not isinstance(value, dict):
            continue
        worker_app = value.get("worker_app")
        registration = value.get("registration")
        if not worker_app and isinstance(registration, dict):
            worker_app = registration.get("worker_app")
        if isinstance(worker_app, str) and worker_app:
            records[worker_app] = value
    return records


def _record_adapter_revision(record: dict | None) -> str | None:
    if not isinstance(record, dict):
        return None
    deployment = record.get("deployment")
    if isinstance(deployment, dict) and isinstance(deployment.get("adapter_revision"), str):
        return deployment["adapter_revision"]
    registration = record.get("registration")
    if isinstance(registration, dict) and isinstance(registration.get("adapter_revision"), str):
        return registration["adapter_revision"]
    return None


def _component_status(client: modal.Client) -> list[dict]:
    records = _registered_worker_records(client)
    result: list[dict] = []
    for component in _component_template():
        function_exists = _function_exists(component["app"], component["function"], client)
        if component["kind"] == "gateway":
            registered = True
            revision = None
            current_revision = True
        else:
            record = records.get(component["app"])
            registered = record is not None
            revision = _record_adapter_revision(record)
            current_revision = revision == EXPECTED_WORKER_ADAPTER_REVISION
        result.append(
            {
                **component,
                "deployed": function_exists and registered and current_revision,
                "registered": registered,
                "adapter_revision": revision,
                "expected_adapter_revision": (
                    EXPECTED_WORKER_ADAPTER_REVISION if component["kind"] == "worker" else None
                ),
                "stale": bool(registered and not current_revision),
            }
        )
    return result


def status() -> dict:
    with _state_lock:
        live = dict(_state)
        live["completed_apps"] = list(_state.get("completed_apps", []))
        live["skipped_apps"] = list(_state.get("skipped_apps", []))
        live["component_errors"] = dict(_state.get("component_errors", {}))
        live["component_states"] = dict(_state.get("component_states", {}))

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


def _deploy_worker(client: modal.Client, module: ModuleType, app_name: str) -> dict:
    try:
        _set_component_state(app_name, "deploying")
        _log(f"deploy worker: {app_name}")
        module.app.deploy(client=client)

        _set_component_state(app_name, "syncing-weights")
        _log(f"sync worker weights: {app_name}")
        sync_result = modal.Function.from_name(app_name, "sync_weights", client=client).remote()
        if not isinstance(sync_result, dict) or not isinstance(sync_result.get("bytes"), int) or sync_result["bytes"] <= 0:
            raise RuntimeError(f"{app_name} 权重同步未返回有效结果")

        _set_component_state(app_name, "warming")
        _log(f"warm worker GPU model: {app_name}")
        warmup = modal.Function.from_name(app_name, "warmup", client=client).remote()
        capability = getattr(module, "CAPABILITY", {})
        expected_model = capability.get("id") if isinstance(capability, dict) else None
        if not isinstance(warmup, dict) or (expected_model and warmup.get("model") != expected_model):
            raise RuntimeError(f"{app_name} GPU 预热校验失败")

        _set_component_state(app_name, "registering")
        _log(f"register worker: {app_name}")
        registration = modal.Function.from_name(app_name, "register", client=client).remote()
        if (
            not isinstance(registration, dict)
            or registration.get("adapter_revision") != EXPECTED_WORKER_ADAPTER_REVISION
        ):
            raise RuntimeError(
                f"{app_name} 注册版本不匹配；期望 {EXPECTED_WORKER_ADAPTER_REVISION}"
            )
        _mark_completed(app_name)
        _set_component_state(app_name, "completed")
        _log(f"worker ready: {app_name}")
        return {"app": app_name, "registered": registration, "skipped": False}
    except Exception as exc:
        _record_component_error(app_name, exc)
        _set_component_state(app_name, "failed")
        _log(f"worker failed: {app_name}: {type(exc).__name__}: {exc}")
        raise


def _run_deploy(client: modal.Client, *, skip_existing: bool = True) -> dict:
    _set_state(
        running=True,
        step="preflight",
        component=None,
        completed_apps=[],
        skipped_apps=[],
        component_errors={},
        component_states={
            **{app_name: "pending" for _module, app_name in WORKERS},
            GATEWAY_APP: "waiting",
        },
        error=None,
    )
    try:
        _preflight(client)
        ensure_source()

        existing = (
            {
                item["app"]
                for item in _component_status(client)
                if item.get("deployed") and item.get("kind") == "worker"
            }
            if skip_existing
            else set()
        )
        for app_name in existing:
            _mark_completed(app_name, skipped=True)
            _set_component_state(app_name, "skipped")
            _log(f"skip ready component: {app_name}")

        results_by_app: dict[str, dict] = {
            app_name: {"app": app_name, "skipped": True} for app_name in existing
        }
        expected_model_ids: set[str] = set()
        pending_workers: list[tuple[ModuleType, str]] = []
        for module_name, app_name in WORKERS:
            module = _load_module(module_name)
            capability = getattr(module, "CAPABILITY", {})
            model_id = capability.get("id") if isinstance(capability, dict) else None
            if isinstance(model_id, str) and model_id:
                expected_model_ids.add(model_id)

            if app_name in existing:
                continue

            pending_workers.append((module, app_name))

        concurrency = min(_worker_concurrency(), max(1, len(pending_workers)))
        _set_state(step="deploy-workers", component=None)
        _log(f"deploy {len(pending_workers)} workers with concurrency={concurrency}")
        worker_failures: dict[str, Exception] = {}
        if pending_workers:
            with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="modal-3d-worker") as pool:
                futures = {
                    pool.submit(_deploy_worker, client, module, app_name): app_name
                    for module, app_name in pending_workers
                }
                for future in as_completed(futures):
                    app_name = futures[future]
                    try:
                        results_by_app[app_name] = future.result()
                    except Exception as exc:  # All workers keep running; failures are summarized afterward.
                        worker_failures[app_name] = exc

        if worker_failures:
            _set_component_state(GATEWAY_APP, "blocked")
            summary = "; ".join(
                f"{app_name}: {type(exc).__name__}: {exc}"
                for app_name, exc in worker_failures.items()
            )
            raise RuntimeError(f"{len(worker_failures)} 个 Worker 部署失败: {summary}")

        try:
            _set_state(step="deploy-gateway", component=GATEWAY_APP)
            _set_component_state(GATEWAY_APP, "deploying")
            _log(f"deploy gateway: {GATEWAY_APP}")
            gateway = _load_module("gateway")
            gateway.app.deploy(client=client)

            _set_state(step="verify", component=GATEWAY_APP)
            _set_component_state(GATEWAY_APP, "verifying")
            _log(f"gateway deployed: {GATEWAY_APP}")
            _log("verify gateway capabilities")
            capabilities = modal.Function.from_name(GATEWAY_APP, "capabilities", client=client).remote()
            model_ids = {
                item.get("id")
                for item in capabilities.get("models", [])
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            }
            missing = sorted(expected_model_ids - model_ids)
            if missing:
                raise RuntimeError("Gateway 缺少已部署模型: " + ", ".join(missing))
            _mark_completed(GATEWAY_APP)
            _set_component_state(GATEWAY_APP, "completed")
            _log(f"gateway ready: {GATEWAY_APP}")
        except Exception as exc:
            _record_component_error(GATEWAY_APP, exc)
            _set_component_state(GATEWAY_APP, "failed")
            raise RuntimeError(f"{GATEWAY_APP} 验证失败: {type(exc).__name__}: {exc}") from exc

        _set_state(running=False, step="ready", component=None, error=None)
        _log(f"suite ready: {len(model_ids)} models")
        return {
            "ok": True,
            "deployed": True,
            "source_commit": SOURCE_COMMIT,
            "workers": [results_by_app[app_name] for _module, app_name in WORKERS],
            "gateway": GATEWAY_APP,
            "models": sorted(model_ids),
        }
    except Exception as exc:
        _set_state(running=False, step="failed", error=f"{type(exc).__name__}: {exc}")
        raise


def deploy() -> dict:
    if not _lock.acquire(blocking=False):
        raise RuntimeError("3D 模型套件正在部署，请等待当前部署完成")
    try:
        return _run_deploy(modal_client.client(), skip_existing=False)
    finally:
        _lock.release()


def _background_deploy(client: modal.Client) -> None:
    try:
        _run_deploy(client)
    except Exception:
        # Full component-specific error details are retained in _state.
        pass
    finally:
        _lock.release()


def start_deploy() -> dict:
    """Start a resumable deployment independently of the HTTP request lifetime."""
    if not _lock.acquire(blocking=False):
        return {"ok": True, "accepted": False, "running": True}
    try:
        client = modal_client.client()
        _set_state(
            running=True,
            step="queued",
            component=None,
            completed_apps=[],
            skipped_apps=[],
            component_errors={},
            component_states={
                **{app_name: "pending" for _module, app_name in WORKERS},
                GATEWAY_APP: "waiting",
            },
            error=None,
        )
        thread = threading.Thread(
            target=_background_deploy,
            args=(client,),
            daemon=True,
            name="modal-3d-deploy",
        )
        thread.start()
        return {"ok": True, "accepted": True, "running": True}
    except Exception:
        _lock.release()
        raise
