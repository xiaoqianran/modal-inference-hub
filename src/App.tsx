import { lazy, Suspense, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import "./App.css";
import {
  agentStatus,
  assetBlob,
  cancelJob,
  clearCredentials,
  connectModal,
  createProject,
  credentialsStatus,
  deleteProject,
  disconnectModal,
  getCapabilities,
  getJob,
  installLocalSam,
  migrateLocalSam,
  getProject,
  listModels,
  listProjects,
  materializeProject,
  modalStatus,
  probeAgent,
  prepareExport,
  projectSourceBlob,
  refineProject,
  saveCredentials,
  savePreparedExport,
  segmentProject,
  setSamMode,
  startAgent,
  startLocalSam,
  uninstallLocalSam,
  stopAgent,
  submitProjectGeneration,
  type AgentInfo,
  type CanonicalAsset,
  type CredentialStatus,
  type GenerationJob,
  type GenerationJobStatus,
  type ModelSpec,
  type Project,
  type RefinementBox,
  type RuntimeCapabilities,
  type SamMode,
  type SamSelection,
} from "./agent";
import { invoke } from "@tauri-apps/api/core";

const GlbViewer = lazy(() => import("./GlbViewer"));
const sleep = (milliseconds: number) => new Promise((resolve) => setTimeout(resolve, milliseconds));
const activeJobStatuses = new Set<GenerationJobStatus>(["running", "connection_required", "cancel_requested"]);
const activeProjectStatuses = new Set<Project["status"]>(["generating", "running", "connection_required", "cancel_requested"]);

function jobIsActive(value: GenerationJob) {
  return activeJobStatuses.has(value.status);
}

function jobActivityLabel(status: GenerationJobStatus) {
  if (status === "connection_required") return "云端连接中断，任务可能仍在运行";
  if (status === "cancel_requested") return "取消请求已发送，等待远端确认";
  return "云端生成中";
}

async function waitForModal(info: AgentInfo, attempts: number) {
  for (let index = 0; index < attempts; index += 1) {
    if ((await modalStatus(info)).connected) return true;
    if (index + 1 < attempts) await sleep(250);
  }
  return false;
}

async function listModelsOrEmpty(info: AgentInfo, cloudConnected: boolean) {
  try {
    return await listModels(info);
  } catch (error) {
    if (cloudConnected) throw error;
    return [] as ModelSpec[];
  }
}

function canonicalFromProject(project: Project): CanonicalAsset | null {
  if (
    !project.scene_id ||
    !project.selection_id ||
    !project.candidate_id ||
    !project.canonical_path ||
    project.canonical_bytes === null
  ) {
    return null;
  }
  return {
    scene_id: project.scene_id,
    selection_id: project.selection_id,
    candidate_id: project.candidate_id,
    canonical_path: project.canonical_path,
    canonical_bytes: project.canonical_bytes,
  };
}

function App() {
  const [agent, setAgent] = useState<AgentInfo | null>(null);
  const [agentMessage, setAgentMessage] = useState("本地代理尚未启动");
  const [tokenId, setTokenId] = useState("");
  const [tokenSecret, setTokenSecret] = useState("");
  const [modalConnected, setModalConnected] = useState(false);
  const [modalMessage, setModalMessage] = useState("尚未连接");
  const [persistence, setPersistence] = useState<CredentialStatus>({ supported: false, stored: false });
  const [remember, setRemember] = useState(false);

  const [models, setModels] = useState<ModelSpec[]>([]);
  const [runtimeCapabilities, setRuntimeCapabilities] = useState<RuntimeCapabilities | null>(null);
  const [localSamActionBusy, setLocalSamActionBusy] = useState(false);
  const [modelId, setModelId] = useState("");
  const [project, setProject] = useState<Project | null>(null);
  const [recentProjects, setRecentProjects] = useState<Project[]>([]);
  const [sourceUrl, setSourceUrl] = useState<string | null>(null);
  const [concept, setConcept] = useState("");
  const [selection, setSelection] = useState<SamSelection | null>(null);
  const [candidateId, setCandidateId] = useState<string | null>(null);
  const [refineMode, setRefineMode] = useState<"positive" | "negative" | null>(null);
  const [refineBoxes, setRefineBoxes] = useState<RefinementBox[]>([]);
  const [dragStart, setDragStart] = useState<{ x: number; y: number } | null>(null);
  const [dragPoint, setDragPoint] = useState<{ x: number; y: number } | null>(null);
  const [canonical, setCanonical] = useState<CanonicalAsset | null>(null);
  const [canonicalUrl, setCanonicalUrl] = useState<string | null>(null);
  const [job, setJob] = useState<GenerationJob | null>(null);
  const [resultUrl, setResultUrl] = useState<string | null>(null);
  const [workflowMessage, setWorkflowMessage] = useState("导入图片并输入要提取的对象。");
  const [busy, setBusy] = useState(false);
  const restoredAgent = useRef<number | null>(null);

  const inTauri = "__TAURI_INTERNALS__" in window;
  const selectedModel = modelId
    ? models.find((model) => model.id === modelId)
    : models.find((model) => model.status !== "disabled") ?? models[0];
  const selectedProfile = selectedModel?.profiles[0];

  useEffect(() => {
    if (!inTauri) return;
    let cancelled = false;
    async function initializeAgent() {
      try {
        setAgentMessage("正在启动本地代理…");
        const [status, saved] = await Promise.all([agentStatus(), credentialsStatus()]);
        const info = status.running ? status : await startAgent();
        await probeAgent(info);
        const connected = await waitForModal(info, saved.stored ? 20 : 1);
        const availableModels = await listModelsOrEmpty(info, connected);
        if (cancelled) return;
        setAgent(info);
        setModels(availableModels);
        setPersistence(saved);
        setRemember(saved.supported);
        setAgentMessage(`本地代理已就绪 · 127.0.0.1:${info.port}`);
        setModalConnected(connected);
        setModalMessage(connected ? "当前会话已连接 · 已从 Windows 恢复" : "尚未连接");
      } catch (error) {
        if (!cancelled) setAgentMessage(error instanceof Error ? error.message : String(error));
      }
    }
    void initializeAgent();
    return () => {
      cancelled = true;
    };
  }, [inTauri]);

  useEffect(() => () => {
    if (sourceUrl) URL.revokeObjectURL(sourceUrl);
  }, [sourceUrl]);
  useEffect(() => () => {
    if (canonicalUrl) URL.revokeObjectURL(canonicalUrl);
  }, [canonicalUrl]);
  useEffect(() => () => {
    if (resultUrl) URL.revokeObjectURL(resultUrl);
  }, [resultUrl]);

  useEffect(() => {
    if (!agent?.running || !agent.port || !modalConnected || restoredAgent.current === agent.port) return;
    restoredAgent.current = agent.port;
    void listProjects(agent)
      .then(async (history) => {
        setRecentProjects(history);
        if (history[0]) await restoreProject(agent, history[0], true);
      })
      .catch((error) => setWorkflowMessage(error instanceof Error ? error.message : String(error)));
  }, [agent, modalConnected]);


  useEffect(() => {
    if (!agent?.running) return;
    void getCapabilities(agent)
      .then(setRuntimeCapabilities)
      .catch((error) => setWorkflowMessage(error instanceof Error ? error.message : String(error)));
  }, [agent, modalConnected]);

  async function refreshProjects(info: AgentInfo) {
    const history = await listProjects(info);
    setRecentProjects(history);
    return history;
  }

  function resetOutput() {
    setJob(null);
    setResultUrl(null);
  }

  function clearWorkspace() {
    setProject(null);
    setSourceUrl(null);
    setConcept("");
    setSelection(null);
    setCandidateId(null);
    setRefineMode(null);
    setRefineBoxes([]);
    setDragStart(null);
    setDragPoint(null);
    setCanonical(null);
    setCanonicalUrl(null);
    resetOutput();
  }

  async function followJob(
    info: AgentInfo,
    initial: GenerationJob,
    restored = false,
    projectId?: string,
  ) {
    let current = initial;
    setJob(current);
    setModelId(current.model);
    if (restored && jobIsActive(current)) {
      setWorkflowMessage(`正在恢复 ${current.model} 的远程任务…`);
    }
    while (jobIsActive(current)) {
      const previousStatus = current.status;
      await sleep(current.status === "connection_required" ? 2000 : 1000);
      current = await getJob(info, current.id);
      setJob(current);
      if (current.status === previousStatus) continue;
      if (current.status === "connection_required") {
        setWorkflowMessage(current.error ?? "云端连接中断，远端任务可能仍在运行；重新连接后会继续恢复。");
      } else if (current.status === "cancel_requested") {
        setWorkflowMessage("取消请求已发送，正在等待远端确认…");
      } else if (current.status === "running" && previousStatus === "connection_required") {
        setWorkflowMessage("云端连接已恢复，继续等待生成结果…");
      }
    }
    if (projectId) {
      const updated = await getProject(info, projectId);
      setProject(updated);
      await refreshProjects(info);
    }
    if (current.status !== "succeeded" || !current.result) {
      setWorkflowMessage(current.error || `任务已结束：${current.status}`);
      return;
    }
    setWorkflowMessage(restored ? "已恢复最近的 3D 结果，正在加载预览…" : "3D 已生成，正在加载预览…");
    const blob = await assetBlob(info, current.result.artifact.path);
    setResultUrl(URL.createObjectURL(blob));
    setWorkflowMessage(restored ? "已恢复最近的 3D 结果。" : "3D 生成完成。");
  }

  async function restoreProject(info: AgentInfo, value: Project, restored = false) {
    setBusy(true);
    try {
      setProject(value);
      setConcept(value.concept ?? "");
      setModelId(value.model ?? "");
      setSelection(null);
      setCandidateId(value.candidate_id);
      setRefineMode(null);
      setRefineBoxes([]);
      setDragStart(null);
      setDragPoint(null);
      resetOutput();

      const source = await projectSourceBlob(info, value.id);
      setSourceUrl(URL.createObjectURL(source));

      const savedCanonical = canonicalFromProject(value);
      setCanonical(savedCanonical);
      if (savedCanonical) {
        setCanonicalUrl(URL.createObjectURL(await assetBlob(info, savedCanonical.canonical_path)));
      } else {
        setCanonicalUrl(null);
      }

      if (value.job_id) {
        const savedJob = await getJob(info, value.job_id);
        await followJob(info, savedJob, restored, value.id);
      } else if (value.status === "segmented") {
        setWorkflowMessage("已恢复项目。候选框未持久化，请重新识别对象。");
      } else if (savedCanonical) {
        setWorkflowMessage("已恢复 Canonical RGBA，可以继续生成 3D。");
      } else {
        setWorkflowMessage("已恢复项目，可以继续识别对象。");
      }
    } finally {
      setBusy(false);
    }
  }

  async function start() {
    try {
      const saved = await credentialsStatus();
      const info = await startAgent();
      await probeAgent(info);
      const connected = await waitForModal(info, saved.stored ? 20 : 1);
      const availableModels = await listModelsOrEmpty(info, connected);
      setAgent(info);
      setModels(availableModels);
      setPersistence(saved);
      setRemember(saved.supported);
      setAgentMessage(`本地代理已就绪 · 127.0.0.1:${info.port}`);
      setModalConnected(connected);
      setModalMessage(connected ? "当前会话已连接 · 已从 Windows 恢复" : "尚未连接");
    } catch (error) {
      setAgentMessage(error instanceof Error ? error.message : String(error));
    }
  }

  async function stop() {
    await stopAgent();
    setAgent({ running: false, port: null, session_token: null });
    setModalConnected(false);
    setAgentMessage("本地代理已停止");
    setModalMessage("尚未连接");
  }

  async function connect() {
    if (!agent?.running) return;
    const credentials = { token_id: tokenId, token_secret: tokenSecret };
    try {
      setModalMessage("正在连接…");
      await connectModal(agent, credentials);
      let stored = persistence.stored;
      let saveFailed = false;
      if (remember && persistence.supported) {
        try {
          await saveCredentials(credentials);
          stored = true;
        } catch {
          saveFailed = true;
        }
      }
      setTokenSecret("");
      setPersistence({ ...persistence, stored });
      setModalConnected(true);
      setModalMessage(saveFailed ? "已连接，但保存 Windows 凭据失败" : stored ? "当前会话已连接 · 已保存到 Windows" : "当前会话已连接");
      try {
        setModels(await listModels(agent));
      } catch (error) {
        setModalMessage(`已连接，但模型 capability 不可用：${error instanceof Error ? error.message : String(error)}`);
      }
    } catch (error) {
      setModalConnected(false);
      setModalMessage(error instanceof Error ? error.message : String(error));
    }
  }

  async function disconnect() {
    if (!agent?.running) return;
    await disconnectModal(agent);
    setModalConnected(false);
    setModalMessage(persistence.stored ? "已断开 · Windows 中仍保留凭据" : "尚未连接");
  }

  async function forget() {
    await clearCredentials();
    if (agent?.running) await disconnectModal(agent);
    setPersistence({ ...persistence, stored: false });
    setModalConnected(false);
    setModalMessage("已删除保存的凭据");
  }

  function localSamProgressLabel() {
    const local = runtimeCapabilities?.sam.local;
    if (!local?.installing) return local?.reason ?? "";
    const speed = local.download_speed_bps && local.download_speed_bps > 0
      ? ` · ${(local.download_speed_bps / 1024 / 1024).toFixed(1)} MiB/s`
      : "";
    const eta = local.download_eta_seconds && local.download_eta_seconds > 0
      ? ` · 剩余约 ${Math.ceil(local.download_eta_seconds / 60)} 分钟`
      : "";
    if (local.step === "checkpoint" && local.downloaded_bytes) {
      const percent = Math.min(100, (local.downloaded_bytes / local.checkpoint_bytes) * 100);
      return `正在同步 SAM 3.1 checkpoint · ${percent.toFixed(1)}%${speed}${eta}`;
    }
    if (local.step === "dependencies") return `正在安装预编译 Torch / CUDA runtime…${speed}${eta}`;
    if (local.step === "health") return "正在启动 Local SAM 并加载模型…";
    return `正在安装 Local SAM runtime…${speed}${eta}`;
  }

  async function refreshLocalCapabilities() {
    if (!agent) return null;
    const state = await getCapabilities(agent);
    setRuntimeCapabilities(state);
    return state;
  }

  async function installLocalRuntime() {
    if (!agent) return;
    const updating = runtimeCapabilities?.sam.local.update_available ?? false;
    try {
      setLocalSamActionBusy(true);
      await installLocalSam(agent);
      setWorkflowMessage(`Local SAM 已开始后台${updating ? "更新" : "安装"}；Cloud SAM 仍可继续使用。`);
      while (true) {
        await sleep(1000);
        const state = await refreshLocalCapabilities();
        if (!state?.sam.local.installing) {
          if (state?.sam.local.error) {
            setWorkflowMessage(`Local SAM 安装失败：${state.sam.local.error}`);
          } else if (state?.sam.local.ready) {
            setWorkflowMessage(`Local SAM 已就绪：${state.sam.local.health?.gpu ?? "NVIDIA GPU"}`);
          } else {
            setWorkflowMessage(state?.sam.local.reason ?? "Local SAM 安装结束。");
          }
          break;
        }
      }
    } catch (error) {
      setWorkflowMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setLocalSamActionBusy(false);
    }
  }

  async function uninstallLocalRuntime() {
    if (!agent || !(runtimeCapabilities?.sam.local.installed || runtimeCapabilities?.sam.local.update_available)) return;
    if (!window.confirm("卸载 Local SAM？将删除 runtime、Torch/CUDA 依赖和 3.5 GB checkpoint；Project 的本地 selection 数据会保留。")) return;
    try {
      setLocalSamActionBusy(true);
      const result = await uninstallLocalSam(agent);
      const state = await refreshLocalCapabilities();
      const gib = result.released_bytes / 1024 / 1024 / 1024;
      const routing = state?.sam.mode === "auto" ? "SAM 已切回 Auto" : "selection 数据已保留";
      setWorkflowMessage(`Local SAM 已卸载，释放 ${gib.toFixed(2)} GiB；${routing}。`);
    } catch (error) {
      setWorkflowMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setLocalSamActionBusy(false);
    }
  }

  async function migrateLocalRuntime() {
    if (!agent || !runtimeCapabilities || runtimeCapabilities.sam.local.installing) return;
    try {
      const selected = await invoke<string | null>("choose_local_sam_directory");
      if (!selected) return;
      setLocalSamActionBusy(true);
      setWorkflowMessage("正在迁移 Local SAM 文件，请勿退出客户端…");
      await migrateLocalSam(agent, selected);
      await refreshLocalCapabilities();
      setWorkflowMessage(`Local SAM 已迁移到：${selected}`);
    } catch (error) {
      setWorkflowMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setLocalSamActionBusy(false);
    }
  }

  async function verifyLocalRuntime() {
    if (!agent) return;
    try {
      setLocalSamActionBusy(true);
      setWorkflowMessage("正在启动 Local SAM 并加载模型…");
      await startLocalSam(agent);
      const state = await refreshLocalCapabilities();
      setWorkflowMessage(
        state?.sam.local.ready
          ? `Local SAM 已就绪：${state.sam.local.health?.gpu ?? "NVIDIA GPU"}`
          : state?.sam.local.reason ?? "Local SAM 未就绪",
      );
    } catch (error) {
      setWorkflowMessage(error instanceof Error ? error.message : String(error));
      await refreshLocalCapabilities();
    } finally {
      setLocalSamActionBusy(false);
    }
  }

  async function changeSamMode(mode: SamMode) {
    if (!agent) return;
    try {
      await setSamMode(agent, mode);
      const state = await getCapabilities(agent);
      setRuntimeCapabilities(state);
      const effective = state.sam.effective;
      setWorkflowMessage(
        effective
          ? `SAM 模式已切换：${mode === "auto" ? `Auto → ${effective}` : effective}`
          : state.sam.local.reason,
      );
    } catch (error) {
      setWorkflowMessage(error instanceof Error ? error.message : String(error));
    }
  }

  async function deleteProjectEntry(item: Project) {
    if (!agent) return;
    if (item.status === "generating") {
      setWorkflowMessage("项目仍在生成中，请先取消任务再删除。");
      return;
    }
    if (!window.confirm(`删除项目“${item.title}”？本地源图片会被删除，远程生成结果会保留。`)) return;
    try {
      setBusy(true);
      await deleteProject(agent, item.id);
      const history = await refreshProjects(agent);
      if (project?.id === item.id) {
        if (history[0]) {
          await restoreProject(agent, history[0]);
        } else {
          clearWorkspace();
        }
      }
      setWorkflowMessage("项目已删除，本地源图片已清理；远程 artifact 保留。");
    } catch (error) {
      setWorkflowMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function chooseImage(file: File | null) {
    if (!agent || !file) return;
    try {
      setBusy(true);
      setWorkflowMessage("正在创建本地项目…");
      const created = await createProject(agent, file);
      setProject(created);
      setSourceUrl(URL.createObjectURL(file));
      setConcept("");
      setSelection(null);
      setCandidateId(null);
      setRefineMode(null);
      setRefineBoxes([]);
      setDragStart(null);
      setDragPoint(null);
      setCanonical(null);
      setCanonicalUrl(null);
      resetOutput();
      await refreshProjects(agent);
      setWorkflowMessage("项目已创建。输入对象名称后开始分割。");
    } catch (error) {
      setWorkflowMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function segment() {
    if (!agent || !project || !concept.trim()) return;
    try {
      setBusy(true);
      const effective = runtimeCapabilities?.sam.effective ?? "cloud";
      setWorkflowMessage(`${effective === "local" ? "Local" : "Cloud"} SAM 正在识别对象…`);
      const value = await segmentProject(agent, project.id, concept.trim());
      setProject(value.project);
      setSelection(value.selection);
      setCandidateId(value.selection.candidates[0]?.candidate_id ?? null);
      setRefineMode(null);
      setRefineBoxes([]);
      setDragStart(null);
      setDragPoint(null);
      setCanonical(null);
      setCanonicalUrl(null);
      resetOutput();
      await refreshProjects(agent);
      setWorkflowMessage(value.selection.candidate_count ? `${value.provider === "local" ? "Local" : "Cloud"} SAM 找到 ${value.selection.candidate_count} 个候选，请选择目标。` : "没有找到候选对象，请换一个描述。");
    } catch (error) {
      setWorkflowMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  function refinementPoint(event: ReactPointerEvent<HTMLDivElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    return {
      x: Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width)),
      y: Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height)),
    };
  }

  function beginRefinement(event: ReactPointerEvent<HTMLDivElement>) {
    if (!refineMode || !selection || event.button !== 0) return;
    const point = refinementPoint(event);
    event.currentTarget.setPointerCapture(event.pointerId);
    setDragStart(point);
    setDragPoint(point);
  }

  function moveRefinement(event: ReactPointerEvent<HTMLDivElement>) {
    if (!refineMode || !dragStart) return;
    setDragPoint(refinementPoint(event));
  }

  function endRefinement(event: ReactPointerEvent<HTMLDivElement>) {
    if (!refineMode || !dragStart) return;
    const end = refinementPoint(event);
    const width = Math.abs(end.x - dragStart.x);
    const height = Math.abs(end.y - dragStart.y);
    if (width >= 0.01 && height >= 0.01) {
      setRefineBoxes((boxes) => [
        ...boxes,
        {
          cx: (end.x + dragStart.x) / 2,
          cy: (end.y + dragStart.y) / 2,
          width,
          height,
          positive: refineMode === "positive",
        },
      ]);
    }
    setDragStart(null);
    setDragPoint(null);
  }

  async function applyRefinement() {
    if (!agent || !project || refineBoxes.length === 0) return;
    try {
      setBusy(true);
      setWorkflowMessage("正在使用提示框 Refine…");
      const value = await refineProject(agent, project.id, refineBoxes);
      setProject(value.project);
      setSelection(value.selection);
      setCandidateId(value.selection.candidates[0]?.candidate_id ?? null);
      setCanonical(null);
      setCanonicalUrl(null);
      resetOutput();
      setRefineBoxes([]);
      setRefineMode(null);
      await refreshProjects(agent);
      setWorkflowMessage(`${value.provider === "local" ? "Local" : "Cloud"} SAM Refine 完成，找到 ${value.selection.candidate_count} 个候选。`);
    } catch (error) {
      setWorkflowMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function materialize() {
    if (!agent || !project || !candidateId) return;
    try {
      setBusy(true);
      setWorkflowMessage("正在生成标准 Canonical RGBA…");
      const value = await materializeProject(agent, project.id, candidateId);
      const blob = await assetBlob(agent, value.canonical.canonical_path);
      setProject(value.project);
      setCanonical(value.canonical);
      setCanonicalUrl(URL.createObjectURL(blob));
      resetOutput();
      await refreshProjects(agent);
      setWorkflowMessage("Canonical RGBA 已确认，可以生成 3D。");
    } catch (error) {
      setWorkflowMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function generate() {
    if (!agent || !project || !canonical || !selectedModel || !selectedProfile) return;
    try {
      setBusy(true);
      resetOutput();
      setWorkflowMessage(`已提交 ${selectedModel.name}，等待云端生成…`);
      const value = await submitProjectGeneration(agent, project.id, selectedModel.id, selectedProfile.id);
      setProject(value.project);
      await refreshProjects(agent);
      await followJob(agent, value.job, false, project.id);
    } catch (error) {
      setWorkflowMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function cancel() {
    if (!agent || !job || !jobIsActive(job) || job.status === "cancel_requested") return;
    try {
      const current = await cancelJob(agent, job.id);
      setJob(current);
      if (project) setProject(await getProject(agent, project.id));
      await refreshProjects(agent);
      setWorkflowMessage(
        current.status === "cancelled"
          ? "任务已取消。"
          : current.error ?? "取消请求已发送，正在等待远端确认…",
      );
    } catch (error) {
      setWorkflowMessage(error instanceof Error ? error.message : String(error));
    }
  }

  async function downloadResult() {
    if (!job?.result || !agent) return;
    try {
      setWorkflowMessage("正在准备 GLB 导出…");
      const prepared = await prepareExport(agent, job.result.artifact.path);
      const saved = await savePreparedExport(prepared.id, `modal-3d-${job.model}.glb`);
      setWorkflowMessage(saved ? `GLB 已保存：${saved}` : "已取消保存。 ");
    } catch (error) {
      setWorkflowMessage(error instanceof Error ? error.message : String(error));
    }
  }

  return (
    <main className="shell">
      <header>
        <div className="header-badge"><span className="badge-dot" />Image → 3D</div>
        <span className="eyebrow">modal-3D</span>
        <h1>三维创作客户端</h1>
        <p>选择对象，生成标准透明资产，再交给云端 GPU 生成 GLB。</p>
      </header>

      <section className="status">
        <span className={agent?.running ? "dot" : "dot idle"} />
        <div className="status-copy">
          <strong>{agent?.running ? "本地代理运行正常" : "正在初始化运行环境"}</strong>
          <p>{inTauri ? agentMessage : "请通过 `npm run desktop:dev` 打开桌面客户端。"}</p>
        </div>
        {inTauri && <button onClick={agent?.running ? stop : start}>{agent?.running ? "停止代理" : "启动代理"}</button>}
      </section>

      {!modalConnected && (
        <section className="credentials">
          <div>
            <span className="eyebrow">云端服务</span>
            <h2>连接 Modal</h2>
            <p>凭据只交给本地 Agent；Windows 可使用凭据管理器安全保存。</p>
          </div>
          <div className="form">
            <label>令牌 ID<input value={tokenId} onChange={(event) => setTokenId(event.target.value)} autoComplete="off" placeholder="ak-…" /></label>
            <label>令牌密钥<input type="password" value={tokenSecret} onChange={(event) => setTokenSecret(event.target.value)} autoComplete="off" placeholder="••••••••••••" /></label>
            {persistence.supported && <label className="remember"><input type="checkbox" checked={remember} onChange={(event) => setRemember(event.target.checked)} />在这台 Windows 电脑上记住</label>}
            <div className="form-actions">
              <span className="muted">{modalMessage}</span>
              <button disabled={!agent?.running || !tokenId || !tokenSecret} onClick={connect}>连接云端</button>
            </div>
          </div>
        </section>
      )}

      {modalConnected && (
        <>
          <section className="cloud-bar">
            <span className="connected">● Modal 已连接</span>
            <div className="buttons">
              {persistence.stored && <button className="secondary" onClick={forget}>删除已保存凭据</button>}
              <button className="secondary" onClick={disconnect}>断开</button>
            </div>
          </section>

          <section className="workspace">
            <div className="workspace-head">
              <div>
                <span className="eyebrow">Workspace</span>
                <h2>{project?.title ?? "从图片生成 3D"}</h2>
              </div>
              <span className="workflow-message">{workflowMessage}</span>
            </div>

            {recentProjects.length > 0 && (
              <div className="project-history">
                <span>最近项目</span>
                {recentProjects.slice(0, 6).map((item) => (
                  <div className="project-chip" key={item.id}>
                    <button
                      className={`project-open ${item.id === project?.id ? "active" : ""}`}
                      disabled={busy}
                      onClick={() => agent && void restoreProject(agent, item)}
                    >
                      <strong>{item.title}</strong>
                      <small>{item.status}</small>
                    </button>
                    <button
                      className="project-delete"
                      disabled={busy || activeProjectStatuses.has(item.status)}
                      title={activeProjectStatuses.has(item.status) ? "请先等待远程任务结束或完成取消" : "删除项目"}
                      onClick={() => void deleteProjectEntry(item)}
                    >
                      ×
                    </button>
                  </div>
                ))}
              </div>
            )}


            {runtimeCapabilities && (
              <div className="sam-mode-bar">
                <div>
                  <strong>SAM Provider</strong>
                  <span>
                    {runtimeCapabilities.sam.mode === "auto"
                      ? `Auto → ${runtimeCapabilities.sam.effective ?? "不可用"}`
                      : runtimeCapabilities.sam.mode}
                  </span>
                </div>
                <div className="sam-mode-buttons">
                  {(["auto", "cloud", "local"] as SamMode[]).map((mode) => (
                    <button
                      key={mode}
                      className={runtimeCapabilities.sam.mode === mode ? "active" : ""}
                      disabled={busy || localSamActionBusy || (mode === "local" && !runtimeCapabilities.sam.local.available)}
                      title={mode === "local" ? runtimeCapabilities.sam.local.reason : undefined}
                      onClick={() => void changeSamMode(mode)}
                    >
                      {mode === "auto" ? "Auto" : mode === "cloud" ? "Cloud" : "Local"}
                    </button>
                  ))}
                </div>
                <small>{localSamProgressLabel()}</small>
                <small className="local-sam-path">存储：{runtimeCapabilities.sam.local.root_path}</small>
                <div className="local-sam-actions">
                  {runtimeCapabilities.sam.local.hardware_eligible && runtimeCapabilities.sam.local.disk_eligible && !runtimeCapabilities.sam.local.installed && (
                    <button
                      disabled={localSamActionBusy || runtimeCapabilities.sam.local.installing}
                      onClick={() => void installLocalRuntime()}
                    >
                      {runtimeCapabilities.sam.local.installing
                        ? "处理中…"
                        : runtimeCapabilities.sam.local.update_available
                          ? "更新 Local"
                          : "安装 Local"}
                    </button>
                  )}
                  {runtimeCapabilities.sam.local.installed && !runtimeCapabilities.sam.local.ready && (
                    <button disabled={localSamActionBusy} onClick={() => void verifyLocalRuntime()}>
                      启动并验证
                    </button>
                  )}
                  {runtimeCapabilities.sam.local.ready && (
                    <span className="local-ready">● Local ready</span>
                  )}
                  {(runtimeCapabilities.sam.local.installed || runtimeCapabilities.sam.local.update_available) && (
                    <button disabled={localSamActionBusy} onClick={() => void uninstallLocalRuntime()}>
                      卸载 Local
                    </button>
                  )}
                  <button disabled={localSamActionBusy || runtimeCapabilities.sam.local.installing} onClick={() => void migrateLocalRuntime()}>
                    迁移目录
                  </button>
                </div>
              </div>
            )}

            <div className="workflow-grid">
              <div className="panel">
                <div className="panel-title"><span>1</span><strong>选择对象</strong></div>
                <label className="upload"><input disabled={busy} type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => void chooseImage(event.target.files?.[0] ?? null)} />{sourceUrl ? "新建项目" : "选择图片"}</label>
                <div className="concept-row">
                  <input value={concept} onChange={(event) => setConcept(event.target.value)} placeholder="例如：cup、chair、plant" onKeyDown={(event) => { if (event.key === "Enter") void segment(); }} />
                  <button disabled={busy || !project || !concept.trim()} onClick={segment}>识别</button>
                </div>

                {sourceUrl && (
                  <div
                    className={`image-stage ${refineMode ? "refining" : ""}`}
                    style={selection ? { aspectRatio: `${selection.image_size[0]} / ${selection.image_size[1]}` } : undefined}
                    onPointerDown={beginRefinement}
                    onPointerMove={moveRefinement}
                    onPointerUp={endRefinement}
                  >
                    <img src={sourceUrl} alt="源图片" />
                    {selection?.candidates.map((candidate) => {
                      const [x0, y0, x1, y1] = candidate.model_bbox_xyxy_norm;
                      return <button key={candidate.candidate_id} className={`candidate ${candidateId === candidate.candidate_id ? "selected" : ""}`} style={{ left: `${x0 * 100}%`, top: `${y0 * 100}%`, width: `${(x1 - x0) * 100}%`, height: `${(y1 - y0) * 100}%` }} onClick={() => setCandidateId(candidate.candidate_id)} title={`score ${candidate.score.toFixed(3)}`} />;
                    })}
                    {refineBoxes.map((box, index) => (
                      <div
                        key={`${box.positive ? "p" : "n"}-${index}`}
                        className={`refine-box ${box.positive ? "positive" : "negative"}`}
                        style={{
                          left: `${(box.cx - box.width / 2) * 100}%`,
                          top: `${(box.cy - box.height / 2) * 100}%`,
                          width: `${box.width * 100}%`,
                          height: `${box.height * 100}%`,
                        }}
                      />
                    ))}
                    {dragStart && dragPoint && (
                      <div
                        className={`refine-box preview ${refineMode === "negative" ? "negative" : "positive"}`}
                        style={{
                          left: `${Math.min(dragStart.x, dragPoint.x) * 100}%`,
                          top: `${Math.min(dragStart.y, dragPoint.y) * 100}%`,
                          width: `${Math.abs(dragPoint.x - dragStart.x) * 100}%`,
                          height: `${Math.abs(dragPoint.y - dragStart.y) * 100}%`,
                        }}
                      />
                    )}
                  </div>
                )}

                {selection && <div className="candidate-list">{selection.candidates.map((candidate) => <button key={candidate.candidate_id} className={candidateId === candidate.candidate_id ? "active" : ""} onClick={() => setCandidateId(candidate.candidate_id)}>#{candidate.rank + 1} · {(candidate.score * 100).toFixed(1)}%</button>)}</div>}
                {selection && (
                  <div className="refine-toolbar">
                    <span>候选不准？在图上拖框</span>
                    <button className={refineMode === "positive" ? "active positive" : ""} disabled={busy} onClick={() => setRefineMode(refineMode === "positive" ? null : "positive")}>+ 保留</button>
                    <button className={refineMode === "negative" ? "active negative" : ""} disabled={busy} onClick={() => setRefineMode(refineMode === "negative" ? null : "negative")}>− 排除</button>
                    <button disabled={busy || refineBoxes.length === 0} onClick={() => void applyRefinement()}>应用 Refine ({refineBoxes.length})</button>
                    {refineBoxes.length > 0 && <button disabled={busy} onClick={() => setRefineBoxes([])}>清除</button>}
                  </div>
                )}
                <button className="primary full" disabled={busy || !project || !candidateId} onClick={materialize}>确认对象</button>
              </div>

              <div className="panel">
                <div className="panel-title"><span>2</span><strong>选择模型并生成</strong></div>
                {resultUrl ? <Suspense fallback={<div className="glb-viewer"><span className="viewer-message">正在加载 3D 引擎…</span></div>}><GlbViewer url={resultUrl} /></Suspense> : (
                  <div className="canonical-preview">{canonicalUrl ? <img src={canonicalUrl} alt="Canonical RGBA" /> : <div>确认对象后，这里会显示标准透明 RGBA。</div>}</div>
                )}
                {canonical && !resultUrl && <div className="asset-meta"><span>Canonical RGBA</span><strong>{(canonical.canonical_bytes / 1024).toFixed(0)} KiB</strong></div>}

                <div className="model-options">
                  {models.map((model) => (
                    <button key={model.id} className={`model-option ${model.id === selectedModel?.id ? "active" : ""}`} disabled={busy || model.status === "disabled"} onClick={() => { setModelId(model.id); resetOutput(); }}>
                      <div><strong>{model.name}</strong><span>{model.description}</span></div>
                      <div className="model-meta"><span>Warm ~{model.warm_seconds.toFixed(model.warm_seconds < 10 ? 1 : 0)}s</span><span>{model.output === "textured" ? "纹理" : "几何"}</span></div>
                    </button>
                  ))}
                </div>

                {selectedProfile && <div className="profile-row"><span>Profile</span><strong>{selectedProfile.name}</strong></div>}
                {job && jobIsActive(job) ? (
                  <div className="generation-actions">
                    <button className="primary" disabled>{jobActivityLabel(job.status)}…</button>
                    <button className="danger" disabled={job.status === "cancel_requested"} onClick={cancel}>
                      {job.status === "cancel_requested" ? "取消确认中" : "取消"}
                    </button>
                  </div>
                ) : (
                  <button className="primary full" disabled={busy || !project || !canonical || !selectedModel || selectedModel.status === "disabled"} onClick={generate}>使用 {selectedModel?.name ?? "模型"} 生成 GLB</button>
                )}

                {job?.result && (
                  <div className="result-card">
                    <div><span>生成完成</span><strong>{(job.result.artifact.bytes / 1024 / 1024).toFixed(2)} MiB</strong></div>
                    <div className="result-timing">
                      {job.result.timing.inference_s !== undefined && <span>推理 {job.result.timing.inference_s.toFixed(2)}s</span>}
                      {job.result.timing.load_s !== undefined && <span>加载 {job.result.timing.load_s.toFixed(2)}s</span>}
                    </div>
                    <button className="primary" onClick={downloadResult}>下载 GLB</button>
                  </div>
                )}
              </div>
            </div>
          </section>
        </>
      )}
    </main>
  );
}

export default App;
