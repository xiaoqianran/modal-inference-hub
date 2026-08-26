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
import { pollGenerationJob } from "../generationPoller";
export { isJobActive as jobIsActive } from "../generationState";

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
  const pendingRequestRef = useRef<{ key: string; id: string } | null>(null);

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
      const isCurrent = () =>
        pollId === pollIdRef.current && activeProjectIdRef.current === projectId;

      await pollGenerationJob({
        getJob: () => getJob(agent, jobId),
        getArtifact: () => jobArtifactBlob(agent, jobId),
        isCurrent,
        onJob: setJob,
        onTransientError: (error, artifact) => {
          const message = error instanceof Error ? error.message : String(error);
          setWorkflowMessage(
            artifact
              ? `3D 已生成，结果读取暂时失败，正在自动重试：${message}`
              : `任务状态暂时不可用，正在自动重试：${message}`,
          );
        },
        onSucceeded: (value, artifact) => {
          replaceResultUrl(artifact);
          setResultJob(value);
          setResultCanonicalSha(canonicalSha ?? null);
          onResultReady(jobId);
          setWorkflowMessage("3D 生成完成，可以检查并导出 GLB。");
        },
        onTerminal: async (value) => {
          if (value.error) setWorkflowMessage(value.error);
          await Promise.allSettled([refreshRecent(), refreshGenerations(projectId)]);
        },
      });
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
    const requestKey = [project.id, canonical.sha256, selectedModel.id, selectedProfile.id].join(":");
    const pending = pendingRequestRef.current;
    const requestId = pending?.key === requestKey ? pending.id : crypto.randomUUID();
    pendingRequestRef.current = { key: requestKey, id: requestId };
    try {
      const value = await submitProjectGeneration(
        agent,
        project.id,
        selectedModel.id,
        selectedProfile.id,
        requestId,
      );
      pendingRequestRef.current = null;
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
