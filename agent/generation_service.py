from __future__ import annotations

from agent import artifacts, generation
from agent.generation_store import (
    GenerationConflict,
    GenerationIntentStore,
    GenerationSubmissionUnknown,
)
from agent.jobs import JobManager
from agent.projects import ProjectStore


class GenerationRecoveryPending(RuntimeError):
    pass


class GenerationCoordinator:
    """协调 Project、Generation Intent、Job 与远端提交。"""

    def __init__(
        self,
        projects: ProjectStore,
        intents: GenerationIntentStore,
        jobs: JobManager,
    ) -> None:
        self.projects = projects
        self.intents = intents
        self.jobs = jobs

    def _bind_remote_intent(self, intent: dict) -> dict:
        remote_call_id = intent.get("remote_call_id")
        if not remote_call_id:
            raise GenerationSubmissionUnknown("远端任务标识尚未确认，不能自动重提")
        job = self.jobs.create(intent["model"], remote_call_id)
        self.intents.bind_job(intent["request_id"], job["id"])
        return {"project": self.projects.get(intent["project_id"]), "job": job}

    def recover_after_restart(self) -> None:
        for intent in self.intents.recover_after_restart():
            try:
                self._bind_remote_intent(intent)
            except Exception as exc:  # noqa: BLE001 - 保留 remote_created，后续请求可继续恢复。
                print(
                    f"[agent] generation recovery pending request_id={intent['request_id']} "
                    f"type={type(exc).__name__}",
                    flush=True,
                )

    def _upload_canonical(self, project_id: str, descriptor: dict, local_path) -> str:
        uploaded = artifacts.put(local_path.read_bytes(), ".png")
        if (
            uploaded["sha256"] != descriptor["sha256"]
            or uploaded["bytes"] != descriptor["bytes"]
        ):
            raise artifacts.ArtifactValidationError("Canonical RGBA 上传完整性校验失败")
        canonical_path = uploaded["path"]
        self.projects.record_remote_canonical(
            project_id, canonical_path, descriptor["sha256"]
        )
        return canonical_path

    def _ensure_remote_canonical(self, project_id: str) -> tuple[dict, str]:
        descriptor, local_path = self.projects.canonical_local(project_id)
        try:
            _, canonical_path = self.projects.canonical_remote(project_id)
        except RuntimeError:
            return descriptor, self._upload_canonical(project_id, descriptor, local_path)

        # canonical_path 是本地持久化提示，不是远端存在性承诺。Modal Volume
        # 会独立清理文件，因此每次创建付费任务前都验证远端 PNG 的 bytes + SHA。
        # 只有明确的缺失/内容损坏才重传；鉴权、网络等异常必须向上抛出，
        # 防止在连接不确定时误判为“文件已丢失”。
        try:
            remote = artifacts.describe_remote_png(
                canonical_path, expected_bytes=descriptor["bytes"]
            )
        except (FileNotFoundError, artifacts.ArtifactValidationError):
            return descriptor, self._upload_canonical(project_id, descriptor, local_path)
        if remote.get("sha256") != descriptor["sha256"]:
            return descriptor, self._upload_canonical(project_id, descriptor, local_path)
        return descriptor, canonical_path

    def submit(
        self,
        project_id: str,
        request_id: str,
        model: str,
        profile: str,
        seed: int,
    ) -> dict:
        # 参数校验必须发生在 claim 前；失败时不应留下提交占位。
        options = generation.prepare_options(model, profile, seed)
        claim = self.intents.claim(project_id, request_id, model, profile)
        if not claim["claimed"]:
            if claim["job_id"]:
                try:
                    return {
                        "project": self.projects.get(project_id),
                        "job": self.jobs.get(claim["job_id"]),
                    }
                except KeyError as exc:
                    raise GenerationConflict("生成记录对应的本地任务不存在") from exc
            if claim["state"] == "remote_created":
                try:
                    return self._bind_remote_intent(self.intents.get(request_id))
                except Exception as exc:
                    raise GenerationRecoveryPending(
                        "远端任务已记录，本地任务绑定暂未完成；重试该请求会继续恢复。"
                    ) from exc
            raise GenerationConflict("该生成请求正在提交")

        # Canonical 准备/上传发生在真正的 generation submit 之前；这里失败可安全释放。
        # 即使 SQLite 记录了 remote path，也必须确认 Volume 中内容仍真实存在。
        try:
            _descriptor, canonical_path = self._ensure_remote_canonical(project_id)
        except Exception:
            self.intents.release_pre_remote(request_id)
            raise

        # 从这里开始，任何异常都可能发生在远端已产生付费任务之后。
        # 未拿到 call_id 时只能 fail-closed，绝不能自动恢复成 ready 再提交。
        self.intents.begin_remote(request_id)
        try:
            remote = generation.submit(
                model, canonical_path, profile, seed, options=options
            )
        except Exception as exc:
            message = (
                "远端提交结果未确认。为避免重复计费，已暂停该项目再次提交。"
            )
            self.intents.mark_uncertain(request_id, message)
            raise GenerationSubmissionUnknown(message) from exc

        call_id = remote["call_id"]
        try:
            intent = self.intents.mark_remote(request_id, call_id)
        except Exception as exc:
            # call_id 已知但未能持久化：尽力取消，同时保持本地 submitting/uncertain，
            # 不允许自动重提。
            try:
                generation.cancel_call(call_id)
            except Exception as cancel_exc:  # noqa: BLE001 - 不覆盖持久化原始错误。
                print(
                    f"[agent] generation compensation failed call_id={call_id} "
                    f"type={type(cancel_exc).__name__}",
                    flush=True,
                )
            try:
                self.intents.mark_uncertain(
                    request_id,
                    "远端任务已创建，但本地记录失败。已阻止自动重提。",
                )
            except Exception:  # noqa: BLE001 - 项目仍保持 submitting，重启会 fail-closed。
                pass
            raise GenerationSubmissionUnknown(
                "远端任务已创建，但本地记录失败。已阻止自动重提。"
            ) from exc

        # remote_call_id 已持久化后，不再做补偿取消；任何本地绑定失败都可安全恢复。
        try:
            return self._bind_remote_intent(intent)
        except Exception as exc:
            raise GenerationRecoveryPending(
                "远端任务已记录，本地任务绑定暂未完成；重试该请求会继续恢复。"
            ) from exc
