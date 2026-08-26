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
const POLL_INTERVAL_MS = 1_400;
const MAX_RETRY_DELAY_MS = 10_000;

const sleep = (milliseconds: number) =>
  new Promise((resolve) => setTimeout(resolve, milliseconds));
const nextRetryDelay = (current: number) =>
  Math.min(current * 2, MAX_RETRY_DELAY_MS);

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
  refreshGenerations: (projectId: string) => Promise<void>;
  onResultReady: (jobId: string) => void;
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
  refreshGenerations,
  onResultReady,
  setWorkflowMessage,
}: UseGenerationJobOptions) {
  const [job, setJob] = useState<GenerationJob | null>(null);
  const [resultJob, setResultJob] = useState<GenerationJob | null>(null);
  const [resultCanonicalSha, setResultCanonicalSha] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const pollIdRef = useRef(0);
  const activeProjectIdRef = useRef<string | null>(null);
  const submittingRef = useRef(false);

  const resetOutput = useCallback(() => {
    pollIdRef.current += 1;
    setJob(null);
    setResultJob(null);
    setResultCanonicalSha(null);
    activeProjectIdRef.current = null;
    replaceResultUrl(null);
  }, [replaceResultUrl]);

  const pollJob = useCallback(
    async (jobId: string, projectId: string, canonicalSha?: string | null) => {
      if (!agent?.running) return;
      const pollId = ++pollIdRef.current;
      activeProjectIdRef.current = projectId;
      let retryDelay = POLL_INTERVAL_MS;

      while (pollId === pollIdRef.current && activeProjectIdRef.current === projectId) {
        let value: GenerationJob;
        try {
          value = await getJob(agent, jobId);
          retryDelay = POLL_INTERVAL_MS;
        } catch (error) {
          if (pollId !== pollIdRef.current || activeProjectIdRef.current !== projectId) return;
          setWorkflowMessage(
            `任务状态暂时不可用，正在自动重试：${error instanceof Error ? error.message : String(error)}`,
          );
          await sleep(retryDelay);
          retryDelay = nextRetryDelay(retryDelay);
          continue;
        }

        if (pollId !== pollIdRef.current || activeProjectIdRef.current !== projectId) return;
        setJob(value);
        if (!jobIsActive(value)) {
          if (value.status === "succeeded" && value.result) {
            try {
              replaceResultUrl(await jobArtifactBlob(agent, jobId));
            } catch (error) {
              if (pollId !== pollIdRef.current || activeProjectIdRef.current !== projectId) return;
              setWorkflowMessage(
                `3D 已生成，结果读取暂时失败，正在自动重试：${error instanceof Error ? error.message : String(error)}`,
              );
              await sleep(retryDelay);
              retryDelay = nextRetryDelay(retryDelay);
              continue;
            }
            setResultJob(value);
            setResultCanonicalSha(canonicalSha ?? null);
            onResultReady(jobId);
            setWorkflowMessage("3D 生成完成，可以检查并导出 GLB。");
          } else if (value.error) {
            setWorkflowMessage(value.error);
          }
          await Promise.allSettled([refreshRecent(), refreshGenerations(projectId)]);
          return;
        }
        await sleep(POLL_INTERVAL_MS);
      }
    },
    [agent, onResultReady, refreshGenerations, refreshRecent, replaceResultUrl, setWorkflowMessage],
  );

  const restoreJob = useCallback(
    (value: GenerationJob | null, projectId: string | null) => {
      setJob(value);
      activeProjectIdRef.current = projectId;
    },
    [],
  );

  const restoreResult = useCallback(
    (value: GenerationJob | null, canonicalSha?: string | null) => {
      setResultJob(value?.result ? value : null);
      setResultCanonicalSha(value?.result ? canonicalSha ?? null : null);
    },
    [],
  );

  const generate = useCallback(async () => {
    if (!agent?.running || !project || !canonical || !selectedModel || !selectedProfile) {
      return false;
    }
    if (!modalConnected || submittingRef.current) return false;

    submittingRef.current = true;
    setSubmitting(true);
    pollIdRef.current += 1;
    setWorkflowMessage("正在上传一次 Canonical RGBA 并提交 3D 任务…");
    try {
      const value = await submitProjectGeneration(
        agent,
        project.id,
        selectedModel.id,
        selectedProfile.id,
        crypto.randomUUID(),
      );
      setProject(value.project);
      setJob(value.job);
      activeProjectIdRef.current = value.project.id;
      setWorkflowMessage("Canonical 已上传，云端只负责 3D 重构。");
      void refreshGenerations(value.project.id);
      void pollJob(value.job.id, value.project.id, canonical.sha256);
      return true;
    } catch (error) {
      setWorkflowMessage(error instanceof Error ? error.message : String(error));
      return false;
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  }, [
    agent,
    canonical,
    modalConnected,
    pollJob,
    project,
    refreshGenerations,
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
      if (!agent?.running || !resultJob?.result) return;
      try {
        const prepared = await prepareExport(agent, resultJob.id);
        await savePreparedExport(prepared.id, `${title || "modal-3d"}.glb`);
      } catch (error) {
        setWorkflowMessage(error instanceof Error ? error.message : String(error));
      }
    },
    [agent, resultJob, setWorkflowMessage],
  );

  return {
    job,
    resultJob,
    resultCanonicalSha,
    submitting,
    resetOutput,
    pollJob,
    restoreJob,
    restoreResult,
    generate,
    cancel,
    downloadResult,
  };
}
