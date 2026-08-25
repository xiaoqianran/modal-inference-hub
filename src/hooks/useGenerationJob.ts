import { useCallback, useRef, useState } from "react";
import {
  cancelJob,
  getJob,
  jobArtifactBlob,
  prepareExport,
  savePreparedExport,
  submitProjectGeneration,
  type AgentInfo,
  type CanonicalAsset,
  type GenerationJob,
  type Project,
} from "../agent";

const ACTIVE_JOB_STATUSES = new Set([
  "running",
  "connection_required",
  "cancel_requested",
]);

const sleep = (milliseconds: number) =>
  new Promise((resolve) => setTimeout(resolve, milliseconds));

export function jobIsActive(job: GenerationJob | null): boolean {
  return job ? ACTIVE_JOB_STATUSES.has(job.status) : false;
}

interface UseGenerationJobOptions {
  agent: AgentInfo | null;
  project: Project | null;
  canonical: CanonicalAsset | null;
  selectedModel: { id: string } | undefined;
  selectedProfile: { id: string } | undefined;
  modalConnected: boolean;
  replaceResultUrl: (value: Blob | null) => void;
  setProject: (value: Project) => void;
  refreshRecent: () => Promise<void>;
  setWorkflowMessage: (value: string) => void;
}

export function useGenerationJob({
  agent,
  project,
  canonical,
  selectedModel,
  selectedProfile,
  modalConnected,
  replaceResultUrl,
  setProject,
  refreshRecent,
  setWorkflowMessage,
}: UseGenerationJobOptions) {
  const [job, setJob] = useState<GenerationJob | null>(null);
  const pollIdRef = useRef(0);
  const activeProjectIdRef = useRef<string | null>(null);

  const resetOutput = useCallback(() => {
    pollIdRef.current += 1;
    setJob(null);
    activeProjectIdRef.current = null;
    replaceResultUrl(null);
  }, [replaceResultUrl]);

  const pollJob = useCallback(
    async (jobId: string, projectId: string) => {
      if (!agent?.running) return;
      const pollId = ++pollIdRef.current;
      activeProjectIdRef.current = projectId;
      try {
        while (pollId === pollIdRef.current && activeProjectIdRef.current === projectId) {
          const value = await getJob(agent, jobId);
          if (pollId !== pollIdRef.current || activeProjectIdRef.current !== projectId) return;
          setJob(value);
          if (!jobIsActive(value)) {
            if (value.status === "succeeded" && value.result) {
              replaceResultUrl(await jobArtifactBlob(agent, jobId));
              setWorkflowMessage("3D 生成完成，可以检查并导出 GLB。");
            } else if (value.error) {
              setWorkflowMessage(value.error);
            }
            await refreshRecent();
            return;
          }
          await sleep(1400);
        }
      } catch (error) {
        if (pollId === pollIdRef.current && activeProjectIdRef.current === projectId) {
          setWorkflowMessage(
            `任务状态读取失败：${error instanceof Error ? error.message : String(error)}`,
          );
        }
      }
    },
    [agent, refreshRecent, replaceResultUrl, setWorkflowMessage],
  );

  const restoreJob = useCallback(
    (value: GenerationJob | null, projectId: string | null) => {
      setJob(value);
      activeProjectIdRef.current = projectId;
    },
    [],
  );

  const generate = useCallback(async () => {
    if (!agent?.running || !project || !canonical || !selectedModel || !selectedProfile) {
      return false;
    }
    if (!modalConnected) return false;

    resetOutput();
    setWorkflowMessage("正在上传一次 Canonical RGBA 并提交 3D 任务…");
    try {
      const value = await submitProjectGeneration(
        agent,
        project.id,
        selectedModel.id,
        selectedProfile.id,
      );
      setProject(value.project);
      setJob(value.job);
      activeProjectIdRef.current = value.project.id;
      setWorkflowMessage("Canonical 已上传，云端只负责 3D 重构。");
      void pollJob(value.job.id, value.project.id);
      return true;
    } catch (error) {
      setWorkflowMessage(error instanceof Error ? error.message : String(error));
      return false;
    }
  }, [
    agent,
    canonical,
    modalConnected,
    pollJob,
    project,
    resetOutput,
    selectedModel,
    selectedProfile,
    setProject,
    setWorkflowMessage,
  ]);

  const cancel = useCallback(async () => {
    if (!agent?.running || !job) return;
    try {
      setJob(await cancelJob(agent, job.id));
      setWorkflowMessage("取消请求已发送。");
    } catch (error) {
      setWorkflowMessage(error instanceof Error ? error.message : String(error));
    }
  }, [agent, job, setWorkflowMessage]);

  const downloadResult = useCallback(
    async (title?: string) => {
      if (!agent?.running || !job?.result) return;
      try {
        const prepared = await prepareExport(agent, job.id);
        await savePreparedExport(prepared.id, `${title || "modal-3d"}.glb`);
      } catch (error) {
        setWorkflowMessage(error instanceof Error ? error.message : String(error));
      }
    },
    [agent, job, setWorkflowMessage],
  );

  return { job, resetOutput, pollJob, restoreJob, generate, cancel, downloadResult };
}
