import { lazy, Suspense, useCallback, useEffect, useRef, useState, type Dispatch, type PointerEvent as ReactPointerEvent, type SetStateAction } from "react";
import "./App.css";
import {
  cancelJob,
  createProject,
  deleteProject,
  getJob,
  getProject,
  getProjectComponents,
  getPreprocessStatus,
  jobArtifactBlob,
  listProjects,
  prepareExport,
  preprocessProject,
  projectCanonicalBlob,
  projectSelectionBlob,
  projectSourceBlob,
  savePreparedExport,
  selectProjectComponents,
  submitProjectGeneration,
  type CanonicalAsset,
  type ComponentState,
  type GenerationJob,
  type GenerationJobStatus,
  type ModelDownloadState,
  type PreprocessResult,
  type Project,
} from "./agent";
import SettingsPanel from "./SettingsPanel";
import { useRuntimeController } from "./useRuntimeController";

const GlbViewer = lazy(() => import("./GlbViewer"));
const sleep = (milliseconds: number) => new Promise((resolve) => setTimeout(resolve, milliseconds));
const activeJobStatuses = new Set<GenerationJobStatus>([
  "running",
  "connection_required",
  "cancel_requested",
]);
const activeProjectStatuses = new Set<Project["status"]>([
  "generating",
  "running",
  "connection_required",
  "cancel_requested",
]);

function jobIsActive(value: GenerationJob) {
  return activeJobStatuses.has(value.status);
}

function jobActivityLabel(status: GenerationJobStatus) {
  if (status === "connection_required") return "云端连接中断，任务可能仍在运行";
  if (status === "cancel_requested") return "取消请求已发送，等待远端确认";
  return "云端生成中";
}

function projectStatusLabel(status: Project["status"]) {
  const labels: Record<Project["status"], string> = {
    draft: "待抠图",
    segmented: "旧项目",
    ready: "可生成",
    generating: "提交中",
    running: "生成中",
    connection_required: "等待连接",
    cancel_requested: "取消中",
    succeeded: "已完成",
    failed: "失败",
    cancelled: "已取消",
    expired: "已过期",
  };
  return labels[status];
}

function canonicalFromProject(project: Project): CanonicalAsset | null {
  if (!project.canonical_id || !project.canonical_sha256 || project.canonical_bytes === null) return null;
  return {
    id: project.canonical_id,
    role: "canonical-rgba",
    mime: "image/png",
    bytes: project.canonical_bytes,
    sha256: project.canonical_sha256,
  };
}

function App() {
  const runtimeController = useRuntimeController();
  const { agent, modalConnected, models } = runtimeController;
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [modelId, setModelId] = useState("");
  const [project, setProject] = useState<Project | null>(null);
  const [recentProjects, setRecentProjects] = useState<Project[]>([]);
  const [sourceUrl, setSourceUrl] = useState<string | null>(null);
  const [matteUrl, setMatteUrl] = useState<string | null>(null);
  const [canonical, setCanonical] = useState<CanonicalAsset | null>(null);
  const [canonicalUrl, setCanonicalUrl] = useState<string | null>(null);
  const [preprocessMeta, setPreprocessMeta] = useState<PreprocessResult["preprocess"] | null>(null);
  const [modelDownload, setModelDownload] = useState<ModelDownloadState | null>(null);
  const [componentState, setComponentState] = useState<ComponentState | null>(null);
  const [selectionBox, setSelectionBox] = useState<{ start: [number, number]; current: [number, number] } | null>(null);
  const [job, setJob] = useState<GenerationJob | null>(null);
  const [resultUrl, setResultUrl] = useState<string | null>(null);
  const [workflowMessage, setWorkflowMessage] = useState(
    "选择图片后，在本机完成 rembg 抠图和 Canonical 规范化。",
  );
  const [busy, setBusy] = useState(false);
  const restoredAgent = useRef<number | null>(null);
  const selectionRequestRef = useRef(false);

  const selectedModel = modelId
    ? models.find((model) => model.id === modelId)
    : models.find((model) => model.status !== "disabled") ?? models[0];
  const selectedProfile = selectedModel?.profiles[0];

  const replaceUrl = useCallback((setter: Dispatch<SetStateAction<string | null>>, blob: Blob | null) => {
    setter((current) => {
      if (current) URL.revokeObjectURL(current);
      return blob ? URL.createObjectURL(blob) : null;
    });
  }, []);

  const resetOutput = useCallback(() => {
    setJob(null);
    replaceUrl(setResultUrl, null);
  }, [replaceUrl]);

  const refreshRecent = useCallback(async () => {
    if (!agent?.running) return;
    try {
      setRecentProjects(await listProjects(agent));
    } catch {
      // Recent projects are auxiliary and must not block the workspace.
    }
  }, [agent]);

  const loadPreview = useCallback(async (value: Project) => {
    if (!agent?.running) return;
    replaceUrl(setSourceUrl, await projectSourceBlob(agent, value.id));
    const savedCanonical = canonicalFromProject(value);
    setCanonical(savedCanonical);
    setPreprocessMeta(null);
    if (savedCanonical) {
      const [matte, canonicalBlob, components] = await Promise.all([
        projectSelectionBlob(agent, value.id).catch(() => null),
        projectCanonicalBlob(agent, value.id),
        getProjectComponents(agent, value.id).catch(() => null),
      ]);
      replaceUrl(setMatteUrl, matte);
      replaceUrl(setCanonicalUrl, canonicalBlob);
      setComponentState(components?.component_state ?? null);
    } else {
      replaceUrl(setMatteUrl, null);
      replaceUrl(setCanonicalUrl, null);
      setComponentState(null);
    }
  }, [agent, replaceUrl]);

  const restoreProject = useCallback(async (projectId: string) => {
    if (!agent?.running) return;
    setBusy(true);
    try {
      const value = await getProject(agent, projectId);
      setProject(value);
      await loadPreview(value);
      resetOutput();
      if (value.model) setModelId(value.model);
      if (value.job_id) {
        const restored = await getJob(agent, value.job_id);
        setJob(restored);
        if (restored.result) {
          replaceUrl(setResultUrl, await jobArtifactBlob(agent, restored.id));
        }
      }
      setWorkflowMessage(
        value.canonical_id
          ? "已恢复本地 Canonical，可继续生成。"
          : "已恢复原图，请执行本地 rembg 抠图。",
      );
    } catch (error) {
      setWorkflowMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }, [agent, loadPreview, replaceUrl, resetOutput]);

  useEffect(() => {
    if (!modelId && models.length) {
      setModelId(models.find((model) => model.status !== "disabled")?.id ?? models[0].id);
    }
  }, [modelId, models]);

  useEffect(() => {
    void refreshRecent();
  }, [refreshRecent]);

  useEffect(() => {
    if (!agent?.running || !agent.port || restoredAgent.current === agent.port) return;
    restoredAgent.current = agent.port;
    void listProjects(agent)
      .then((items) => {
        setRecentProjects(items);
        if (items[0]) void restoreProject(items[0].id);
      })
      .catch(() => undefined);
  }, [agent, restoreProject]);

  useEffect(() => () => {
    if (sourceUrl) URL.revokeObjectURL(sourceUrl);
    if (matteUrl) URL.revokeObjectURL(matteUrl);
    if (canonicalUrl) URL.revokeObjectURL(canonicalUrl);
    if (resultUrl) URL.revokeObjectURL(resultUrl);
  }, [sourceUrl, matteUrl, canonicalUrl, resultUrl]);

  async function runLocalPreprocess(target: Project) {
    if (!agent?.running) return;
    const cached = runtimeController.runtime?.preprocessing.model_downloaded;
    setWorkflowMessage(
      cached
        ? "正在本机执行 birefnet-general 抠图…"
        : "首次使用：正在准备 birefnet-general 本地模型…",
    );

    let timer: number | null = null;
    let polling = !cached;
    const updateDownload = async () => {
      if (!agent?.running || !polling) return;
      try {
        const status = await getPreprocessStatus(agent);
        setModelDownload(status.download);
        if (status.download.status === "downloading") {
          const percent = Math.floor(status.download.progress * 100);
          setWorkflowMessage(`正在下载 birefnet-general · ${percent}%`);
        } else if (status.download.status === "verifying") {
          setWorkflowMessage("模型下载完成，正在校验完整性…");
        } else if (status.download.status === "failed") {
          const suffix = status.download.resumable ? "，下次将继续断点续传" : "";
          setWorkflowMessage(`模型准备失败${suffix}`);
        }
      } catch {
        // The long-running preprocess request remains authoritative; polling is best-effort UI only.
      }
    };

    if (polling) {
      void updateDownload();
      timer = window.setInterval(() => void updateDownload(), 500);
    } else {
      setModelDownload(null);
    }

    try {
      const value = await preprocessProject(agent, target.id);
      setProject(value.project);
      setCanonical(value.canonical);
      setPreprocessMeta(value.preprocess);
      setComponentState(value.component_state);
      const [matte, canonicalBlob] = await Promise.all([
        projectSelectionBlob(agent, target.id),
        projectCanonicalBlob(agent, target.id),
      ]);
      replaceUrl(setMatteUrl, matte);
      replaceUrl(setCanonicalUrl, canonicalBlob);
      resetOutput();
      setWorkflowMessage(
        `本地抠图完成 · ${value.preprocess.provider.toUpperCase()} · ${value.component_state.component_count} 个可选前景 · ${value.preprocess.elapsed_ms.toFixed(0)} ms`,
      );
      await refreshRecent();
    } finally {
      polling = false;
      if (timer !== null) window.clearInterval(timer);
      if (!cached) {
        try {
          const finalStatus = await getPreprocessStatus(agent);
          setModelDownload(finalStatus.download);
        } catch {
          // Keep the last known progress state.
        }
      }
    }
  }

  async function chooseImage(file: File | null) {
    if (!agent?.running || !file) return;
    setBusy(true);
    try {
      const value = await createProject(agent, file);
      setProject(value);
      setCanonical(null);
      setPreprocessMeta(null);
      setComponentState(null);
      setModelDownload(null);
      resetOutput();
      replaceUrl(setSourceUrl, file);
      replaceUrl(setMatteUrl, null);
      replaceUrl(setCanonicalUrl, null);
      setWorkflowMessage("原图已保存在本机，正在自动开始 rembg 预处理…");
      await refreshRecent();
      await runLocalPreprocess(value);
    } catch (error) {
      setWorkflowMessage(
        `${error instanceof Error ? error.message : String(error)}；原图已保留，可直接重试本地抠图。`,
      );
    } finally {
      setBusy(false);
    }
  }

  async function preprocess() {
    if (!agent?.running || !project) return;
    setBusy(true);
    try {
      await runLocalPreprocess(project);
    } catch (error) {
      setWorkflowMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function applyComponentSelection(selectedIds: string[]) {
    if (selectionRequestRef.current || !agent?.running || !project || !componentState || selectedIds.length === 0) return;
    if (job && jobIsActive(job)) {
      setWorkflowMessage("远程生成任务活动期间不能修改前景选择。");
      return;
    }
    selectionRequestRef.current = true;
    setBusy(true);
    try {
      const value = await selectProjectComponents(agent, project.id, selectedIds);
      setProject(value.project);
      setCanonical(value.canonical);
      setComponentState(value.component_state);
      const [selectionBlob, canonicalBlob] = await Promise.all([
        projectSelectionBlob(agent, project.id),
        projectCanonicalBlob(agent, project.id),
      ]);
      replaceUrl(setMatteUrl, selectionBlob);
      replaceUrl(setCanonicalUrl, canonicalBlob);
      resetOutput();
      const count = value.component_state.selected_component_ids.length;
      const elapsed = value.component_state.selection_elapsed_ms;
      setWorkflowMessage(
        `已保留 ${count}/${value.component_state.component_count} 个前景${elapsed !== undefined ? ` · ${elapsed.toFixed(0)} ms` : ""}`,
      );
      await refreshRecent();
    } catch (error) {
      setWorkflowMessage(error instanceof Error ? error.message : String(error));
    } finally {
      selectionRequestRef.current = false;
      setBusy(false);
    }
  }

  function toggleComponent(componentId: string) {
    if (busy || selectionRequestRef.current || !componentState) return;
    const selected = new Set(componentState.selected_component_ids);
    if (selected.has(componentId)) {
      if (selected.size === 1) {
        setWorkflowMessage("至少保留一个前景组件。");
        return;
      }
      selected.delete(componentId);
    } else {
      selected.add(componentId);
    }
    void applyComponentSelection(
      componentState.components.filter((item) => selected.has(item.id)).map((item) => item.id),
    );
  }

  function selectAllComponents() {
    if (busy || selectionRequestRef.current || !componentState) return;
    void applyComponentSelection(componentState.components.map((item) => item.id));
  }

  function imagePoint(event: ReactPointerEvent<SVGSVGElement>): [number, number] | null {
    if (!componentState) return null;
    const matrix = event.currentTarget.getScreenCTM();
    if (!matrix) return null;
    const point = event.currentTarget.createSVGPoint();
    point.x = event.clientX;
    point.y = event.clientY;
    const transformed = point.matrixTransform(matrix.inverse());
    return [
      Math.max(0, Math.min(componentState.source_size[0], transformed.x)),
      Math.max(0, Math.min(componentState.source_size[1], transformed.y)),
    ];
  }

  function beginBoxSelection(event: ReactPointerEvent<SVGSVGElement>) {
    if (busy || !componentState || (job && jobIsActive(job))) return;
    const point = imagePoint(event);
    if (!point) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    setSelectionBox({ start: point, current: point });
  }

  function moveBoxSelection(event: ReactPointerEvent<SVGSVGElement>) {
    if (!selectionBox) return;
    const point = imagePoint(event);
    if (point) setSelectionBox((current) => current ? { ...current, current: point } : null);
  }

  function finishBoxSelection(event: ReactPointerEvent<SVGSVGElement>) {
    if (!selectionBox || !componentState) return;
    const point = imagePoint(event) ?? selectionBox.current;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    const x1 = Math.min(selectionBox.start[0], point[0]);
    const y1 = Math.min(selectionBox.start[1], point[1]);
    const x2 = Math.max(selectionBox.start[0], point[0]);
    const y2 = Math.max(selectionBox.start[1], point[1]);
    setSelectionBox(null);
    if (x2 - x1 < 5 || y2 - y1 < 5) return;

    const hit = componentState.components.filter((item) => {
      const [bx1, by1, bx2, by2] = item.bbox;
      const centerX = (bx1 + bx2) / 2;
      const centerY = (by1 + by2) / 2;
      if (centerX >= x1 && centerX <= x2 && centerY >= y1 && centerY <= y2) return true;
      const intersection = Math.max(0, Math.min(x2, bx2) - Math.max(x1, bx1))
        * Math.max(0, Math.min(y2, by2) - Math.max(y1, by1));
      const bboxArea = Math.max(1, (bx2 - bx1) * (by2 - by1));
      return intersection / bboxArea >= 0.25;
    });
    if (!hit.length) {
      setWorkflowMessage("框选区域没有命中可选前景组件。");
      return;
    }
    void applyComponentSelection(hit.map((item) => item.id));
  }

  const pollJob = useCallback(async (jobId: string) => {
    if (!agent?.running) return;
    while (true) {
      const value = await getJob(agent, jobId);
      setJob(value);
      if (!jobIsActive(value)) {
        if (value.status === "succeeded" && value.result) {
          replaceUrl(setResultUrl, await jobArtifactBlob(agent, jobId));
          setWorkflowMessage("3D 生成完成。");
        } else if (value.error) {
          setWorkflowMessage(value.error);
        }
        await refreshRecent();
        return;
      }
      await sleep(1400);
    }
  }, [agent, refreshRecent, replaceUrl]);

  async function generate() {
    if (!agent?.running || !project || !canonical || !selectedModel || !selectedProfile) return;
    if (!modalConnected) {
      setSettingsOpen(true);
      return;
    }
    setBusy(true);
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
      setWorkflowMessage("Canonical 已上传，云端只负责 3D 重构。");
      void pollJob(value.job.id);
    } catch (error) {
      setWorkflowMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function cancel() {
    if (!agent?.running || !job) return;
    try {
      setJob(await cancelJob(agent, job.id));
      setWorkflowMessage("取消请求已发送。");
    } catch (error) {
      setWorkflowMessage(error instanceof Error ? error.message : String(error));
    }
  }

  async function downloadResult() {
    if (!agent?.running || !job?.result) return;
    try {
      const prepared = await prepareExport(agent, job.id);
      await savePreparedExport(prepared.id, `${project?.title || "modal-3d"}.glb`);
    } catch (error) {
      setWorkflowMessage(error instanceof Error ? error.message : String(error));
    }
  }

  async function removeProject(value: Project) {
    if (!agent?.running) return;
    if (activeProjectStatuses.has(value.status)) {
      setWorkflowMessage("该项目仍有远程任务活动，请先等待或取消。");
      return;
    }
    try {
      await deleteProject(agent, value.id);
      if (project?.id === value.id) {
        setProject(null);
        setCanonical(null);
        setPreprocessMeta(null);
        setComponentState(null);
        resetOutput();
        replaceUrl(setSourceUrl, null);
        replaceUrl(setMatteUrl, null);
        replaceUrl(setCanonicalUrl, null);
      }
      await refreshRecent();
    } catch (error) {
      setWorkflowMessage(error instanceof Error ? error.message : String(error));
    }
  }

  const stage = resultUrl || job?.status === "succeeded" ? 3 : canonical ? 2 : project ? 1 : 0;
  const preprocessHint = !project
    ? "选择 PNG / JPEG / WebP 后会自动本地抠图"
    : canonical
      ? "本地抠图完成，原图仍未上传"
      : "预处理失败时可在这里重试；首次使用会准备本地模型";
  const generationHint = !canonical
    ? "先完成本地 rembg 预处理"
    : !modalConnected
      ? "Canonical 已准备好；连接 Modal 后再生成"
      : !selectedModel
        ? "暂无可用模型"
        : "点击生成时仅上传一次 1024×1024 Canonical RGBA";

  return (
    <div className="app-shell">
      <header className="app-header">
        <div>
          <span className="eyebrow">Local-first 2D → Cloud 3D</span>
          <h1>modal-3D</h1>
        </div>
        <div className="header-actions">
          <span className={`connection-dot ${modalConnected ? "online" : ""}`} />
          <span>{modalConnected ? "Modal 已连接" : "Modal 未连接"}</span>
          <button type="button" className="quiet-button" onClick={() => setSettingsOpen(true)}>设置</button>
        </div>
      </header>

      <main className="main-layout">
        <aside className="project-sidebar">
          <div className="sidebar-title"><strong>最近项目</strong><span>{recentProjects.length}</span></div>
          <div className="recent-projects">
            {recentProjects.map((item) => (
              <div key={item.id} className={`recent-project ${project?.id === item.id ? "active" : ""}`}>
                <button type="button" onClick={() => void restoreProject(item.id)} disabled={busy}>
                  <strong>{item.title}</strong><span>{projectStatusLabel(item.status)}</span>
                </button>
                <button type="button" className="delete-project" onClick={() => void removeProject(item)} disabled={busy || activeProjectStatuses.has(item.status)}>×</button>
              </div>
            ))}
          </div>
        </aside>

        <section className="workspace">
          <div className="workflow-progress">
            {["原图", "本地抠图", "3D 生成"].map((label, index) => (
              <div key={label} className={stage >= index + 1 ? "active" : ""}>
                <span>{index + 1}</span><strong>{label}</strong>
              </div>
            ))}
          </div>
          <p className="workflow-message">{workflowMessage}</p>

          <div className="workspace-columns">
            <div className="panel">
              <div className="panel-title"><span>1</span><strong>本地 rembg 预处理</strong></div>
              <label className="upload">
                <input disabled={busy || !agent?.running} type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => void chooseImage(event.target.files?.[0] ?? null)} />
                {sourceUrl ? "选择新图片" : "选择图片"}
              </label>

              {modelDownload && modelDownload.status !== "ready" && (modelDownload.downloaded_bytes > 0 || modelDownload.status === "failed" || modelDownload.status === "verifying") ? (
                <div className="download-progress model-download-progress">
                  <div>
                    <span>{modelDownload.status === "verifying" ? "正在校验模型" : modelDownload.status === "failed" ? "模型下载中断" : "下载 birefnet-general"}</span>
                    <strong>{Math.floor(modelDownload.progress * 100)}%</strong>
                  </div>
                  <progress max={1} value={modelDownload.progress} />
                  <small>
                    {(modelDownload.downloaded_bytes / 1024 / 1024).toFixed(1)} / {(modelDownload.total_bytes / 1024 / 1024).toFixed(1)} MiB
                    {modelDownload.resumable && modelDownload.status === "failed" ? " · 已保留断点，可重试续传" : ""}
                  </small>
                  {modelDownload.error ? <small className="download-error">{modelDownload.error}</small> : null}
                </div>
              ) : null}

              <div className="preprocess-grid">
                <div className="image-stage compact">
                  <span className="preview-label">原图</span>
                  {sourceUrl ? <img src={sourceUrl} alt="Source" /> : <div className="empty-image">原图不会上传到 Modal</div>}
                </div>
                <div className="image-stage compact checker component-stage">
                  <span className="preview-label">当前选择 RGBA · 连通域</span>
                  {matteUrl && componentState ? (
                    <svg
                      className={`component-overlay ${selectionBox ? "dragging" : ""}`}
                      viewBox={`0 0 ${componentState.source_size[0]} ${componentState.source_size[1]}`}
                      role="img"
                      aria-label="可选择的前景组件；可拖框选择"
                      onPointerDown={beginBoxSelection}
                      onPointerMove={moveBoxSelection}
                      onPointerUp={finishBoxSelection}
                      onPointerCancel={() => setSelectionBox(null)}
                    >
                      <image href={matteUrl} x="0" y="0" width={componentState.source_size[0]} height={componentState.source_size[1]} />
                      {componentState.components.map((item, index) => {
                        const [x1, y1, x2, y2] = item.bbox;
                        const selected = componentState.selected_component_ids.includes(item.id);
                        return (
                          <g key={item.id} className={`component-box ${selected ? "selected" : ""}`} onPointerDown={(event) => event.stopPropagation()} onClick={() => toggleComponent(item.id)}>
                            <rect x={x1} y={y1} width={x2 - x1} height={y2 - y1} vectorEffect="non-scaling-stroke" />
                            <text x={x1 + 5} y={Math.max(y1 + 16, 18)}>{index + 1}</text>
                          </g>
                        );
                      })}
                      {selectionBox ? (
                        <rect
                          className="selection-drag-box"
                          x={Math.min(selectionBox.start[0], selectionBox.current[0])}
                          y={Math.min(selectionBox.start[1], selectionBox.current[1])}
                          width={Math.abs(selectionBox.current[0] - selectionBox.start[0])}
                          height={Math.abs(selectionBox.current[1] - selectionBox.start[1])}
                          vectorEffect="non-scaling-stroke"
                        />
                      ) : null}
                    </svg>
                  ) : matteUrl ? <img src={matteUrl} alt="Local rembg matte" /> : <div className="empty-image">rembg 后显示</div>}
                </div>
              </div>

              {componentState ? (
                <div className="component-controls">
                  <div className="component-summary">
                    <div><strong>{componentState.selected_component_ids.length}/{componentState.component_count} 个前景已保留</strong><small>拖框可只保留框中的物体</small></div>
                    <button type="button" className="quiet-button" disabled={busy || componentState.selected_component_ids.length === componentState.component_count} onClick={selectAllComponents}>全部保留</button>
                  </div>
                  <div className="component-list">
                    {componentState.components.map((item, index) => {
                      const selected = componentState.selected_component_ids.includes(item.id);
                      return (
                        <label key={item.id} className={`component-item ${selected ? "selected" : ""}`}>
                          <input type="checkbox" checked={selected} disabled={busy || (selected && componentState.selected_component_ids.length === 1)} onChange={() => toggleComponent(item.id)} />
                          <span>物体 {index + 1}</span>
                          <small>{(item.foreground_ratio * 100).toFixed(1)}%</small>
                        </label>
                      );
                    })}
                  </div>
                  {componentState.ignored_component_count > 0 ? <p className="component-noise">另有 {componentState.ignored_component_count} 个极小 Alpha 碎片未列为独立物体；默认全选时仍保留。</p> : null}
                </div>
              ) : null}

              <button className="primary full" disabled={busy || !project || !agent?.running} onClick={() => void preprocess()}>
                {busy
                  ? "处理中…"
                  : modelDownload?.status === "failed"
                    ? modelDownload.resumable ? "续传模型并重试抠图" : "重试本地抠图"
                    : canonical ? "重新本地抠图" : "本地 rembg 抠图"}
              </button>
              <p className="action-guidance">{preprocessHint}</p>
              {preprocessMeta ? (
                <div className="asset-meta">
                  <span>{preprocessMeta.engine} · {preprocessMeta.provider.toUpperCase()}</span>
                  <strong>{preprocessMeta.elapsed_ms.toFixed(0)} ms</strong>
                </div>
              ) : null}
              <div className="settings-explainer">
                <strong>隐私边界</strong>
                <p>抠图、连通域、多选过滤、Alpha 和 Letterbox 全在本机完成。默认保留全部前景；只有你的选择会改变 Canonical。</p>
              </div>
            </div>

            <div className="panel">
              <div className="panel-title"><span>2</span><strong>Canonical 与 3D</strong></div>
              {resultUrl ? (
                <Suspense fallback={<div className="glb-viewer"><span className="viewer-message">正在加载 3D 引擎…</span></div>}>
                  <GlbViewer url={resultUrl} />
                </Suspense>
              ) : (
                <div className="canonical-preview checker">
                  {canonicalUrl ? <img src={canonicalUrl} alt="1024 Canonical RGBA" /> : <div>本地抠图后显示 1024×1024 Canonical RGBA。</div>}
                </div>
              )}
              {canonical && !resultUrl ? (
                <div className="asset-meta"><span>1024×1024 · RGBA · Letterbox</span><strong>{(canonical.bytes / 1024).toFixed(0)} KiB</strong></div>
              ) : null}

              <div className="model-options">
                {models.map((model) => (
                  <button key={model.id} className={`model-option ${model.id === selectedModel?.id ? "active" : ""}`} disabled={busy || model.status === "disabled"} onClick={() => { setModelId(model.id); resetOutput(); }}>
                    <div><strong>{model.name}</strong><span>{model.description}</span></div>
                    <div className="model-meta"><span>Warm ~{model.warm_seconds.toFixed(model.warm_seconds < 10 ? 1 : 0)}s</span><span>{model.output === "textured" ? "纹理" : "几何"}</span></div>
                  </button>
                ))}
                {!models.length ? (
                  <div className="workspace-recovery">
                    <strong>暂无云端模型</strong>
                    <span>本地抠图不依赖 Modal；生成前再连接即可。</span>
                    <button type="button" className="quiet-button" onClick={() => setSettingsOpen(true)}>打开设置</button>
                  </div>
                ) : null}
              </div>

              {selectedProfile ? <div className="profile-row"><span>Profile</span><strong>{selectedProfile.name}</strong></div> : null}
              {job && jobIsActive(job) ? (
                <div className="generation-actions">
                  <button className="primary" disabled>{jobActivityLabel(job.status)}…</button>
                  <button className="danger" disabled={job.status === "cancel_requested"} onClick={() => void cancel()}>{job.status === "cancel_requested" ? "取消确认中" : "取消"}</button>
                </div>
              ) : (
                <button className="primary full" disabled={busy || !project || !canonical || !selectedModel || selectedModel.status === "disabled"} onClick={() => void generate()}>
                  使用 {selectedModel?.name ?? "模型"} 生成 GLB
                </button>
              )}
              {!job || !jobIsActive(job) ? <p className="action-guidance">{generationHint}</p> : null}

              {job?.result ? (
                <div className="result-card">
                  <div><span>生成完成</span><strong>{(job.result.artifact.bytes / 1024 / 1024).toFixed(2)} MiB</strong></div>
                  <div className="result-timing">
                    {job.result.timing.inference_s !== undefined ? <span>推理 {job.result.timing.inference_s.toFixed(2)}s</span> : null}
                    {job.result.timing.load_s !== undefined ? <span>加载 {job.result.timing.load_s.toFixed(2)}s</span> : null}
                  </div>
                  <button className="primary" onClick={() => void downloadResult()}>下载 GLB</button>
                </div>
              ) : null}
            </div>
          </div>
        </section>
      </main>

      <SettingsPanel open={settingsOpen} onClose={() => setSettingsOpen(false)} controller={runtimeController} />
    </div>
  );
}

export default App;
