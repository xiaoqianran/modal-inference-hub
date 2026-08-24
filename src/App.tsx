import { lazy, Suspense, useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import "./App.css";
import {
  jobArtifactBlob,
  cancelJob,
  createProject,
  deleteProject,
  getJob,
  getProject,
  listProjects,
  materializeProject,
  prepareExport,
  projectCanonicalBlob,
  projectSourceBlob,
  refineProject,
  savePreparedExport,
  segmentProject,
  submitProjectGeneration,
  type AgentInfo,
  type CanonicalAsset,
  type GenerationJob,
  type GenerationJobStatus,
  type Project,
  type RefinementBox,
  type SamSelection,
} from "./agent";
import SettingsPanel from "./SettingsPanel";
import { useRuntimeController } from "./useRuntimeController";

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

function projectStatusLabel(status: Project["status"]) {
  const labels: Record<Project["status"], string> = {
    draft: "待识别", segmented: "已识别", ready: "可生成", generating: "提交中",
    running: "生成中", connection_required: "等待连接", cancel_requested: "取消中",
    succeeded: "已完成", failed: "失败", cancelled: "已取消", expired: "已过期",
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
  const { agent, modalConnected, models, runtime: runtimeCapabilities } = runtimeController;
  const [settingsOpen, setSettingsOpen] = useState(false);

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

  const selectedModel = modelId
    ? models.find((model) => model.id === modelId)
    : models.find((model) => model.status !== "disabled") ?? models[0];
  const selectedProfile = selectedModel?.profiles[0];
  const closeSettings = useCallback(() => setSettingsOpen(false), []);
  const workflowStage = resultUrl || job?.status === "succeeded" ? 4 : job || canonical ? 3 : selection ? 2 : project ? 1 : 0;
  const segmentHint = !project ? "先选择一张主体清晰的图片" : !concept.trim() ? "描述要提取的对象，例如 cup、chair 或 plant" : selection ? `已找到 ${selection.candidate_count} 个候选，可继续识别或调整` : "准备就绪，可以开始识别";
  const candidateHint = !selection ? "完成识别后选择目标候选" : !candidateId ? "点击图片中的框或候选编号" : "目标已选择，确认后会生成透明标准图";
  const generationHint = !canonical ? "先在左侧确认对象" : !selectedModel ? "暂无可用模型，请刷新 Modal 连接" : "模型与推荐参数已就绪";

  useEffect(() => {
    if (runtimeController.initialized && !runtimeController.modalConnected) setSettingsOpen(true);
  }, [runtimeController.initialized, runtimeController.modalConnected]);

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
    const blob = await jobArtifactBlob(info, current.id);
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
        setCanonicalUrl(URL.createObjectURL(await projectCanonicalBlob(info, value.id)));
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
      const blob = await projectCanonicalBlob(agent, project.id);
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
      const prepared = await prepareExport(agent, job.id);
      const saved = await savePreparedExport(prepared.id, `modal-3d-${job.model}.glb`);
      setWorkflowMessage(saved ? `GLB 已保存：${saved}` : "已取消保存。 ");
    } catch (error) {
      setWorkflowMessage(error instanceof Error ? error.message : String(error));
    }
  }

  return (
    <div className="app-shell">
      <aside className="app-sidebar">
        <div className="app-brand"><span className="brand-mark">M3</span><div><strong>modal-3D</strong><span>Studio</span></div></div>
        <nav className="app-nav" aria-label="主导航">
          <button className="active"><span>◆</span>创作工作台</button>
        </nav>
        <div className="sidebar-projects">
          <div className="sidebar-label"><span>最近项目</span><strong>{recentProjects.length}</strong></div>
          {recentProjects.length ? recentProjects.slice(0, 8).map((item) => (
            <div className={`sidebar-project ${item.id === project?.id ? "active" : ""}`} key={item.id}>
              <button className="sidebar-project-open" disabled={busy} onClick={() => agent && void restoreProject(agent, item)}>
                <span>{item.title.slice(0, 1).toUpperCase()}</span><div><strong>{item.title}</strong><small>{projectStatusLabel(item.status)}</small></div>
              </button>
              <button className="sidebar-project-delete" disabled={busy || activeProjectStatuses.has(item.status)} aria-label={`删除项目 ${item.title}`} onClick={() => void deleteProjectEntry(item)}>×</button>
            </div>
          )) : <p className="sidebar-empty">创建的项目会出现在这里</p>}
        </div>
        <div className="sidebar-footer">
          <div className="sidebar-service"><span className={`service-dot ${agent?.running ? "active" : ""}`} /><div><strong>{agent?.running ? "本地服务正常" : "本地服务离线"}</strong><small>{modalConnected ? "Modal 已连接" : "Modal 未连接"}</small></div></div>
          <button className="settings-button" onClick={() => setSettingsOpen(true)}><span>⚙</span>设置</button>
        </div>
      </aside>

      <main className="app-main">
        <header className="app-topbar">
          <div><span className="eyebrow">Creation workspace</span><h1>{project?.title ?? "新建 3D 资产"}</h1></div>
          <div className="topbar-actions">
            <span className={`connection-chip ${modalConnected ? "online" : ""}`}><span />{modalConnected ? "Modal 在线" : "未连接"}</span>
            <button className="icon-button" onClick={() => setSettingsOpen(true)} aria-label="打开设置">⚙</button>
          </div>
        </header>

        {modalConnected ? (
          <section className="workspace">
            <div className="workspace-head">
              <div>
                <span className="eyebrow">Image to 3D</span>
                <h2>{project ? "继续完善你的资产" : "从图片生成 3D"}</h2>
              </div>
              <span className="workflow-message">{workflowMessage}</span>
            </div>

            <ol className="workflow-steps" aria-label="创作进度">
              {["导入图片", "选择对象", "生成 3D", "导出 GLB"].map((label, index) => (
                <li key={label} className={`${workflowStage === index + 1 ? "active" : ""} ${workflowStage > index + 1 ? "done" : ""}`}><span>{workflowStage > index + 1 ? "✓" : index + 1}</span>{label}</li>
              ))}
            </ol>

            <div className="workflow-grid">
              <div className="panel">
                <div className="panel-title"><span>1</span><strong>选择对象</strong></div>
                <label className="upload"><input disabled={busy} type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => void chooseImage(event.target.files?.[0] ?? null)} />{sourceUrl ? "新建项目" : "选择图片"}</label>
                <div className="concept-row">
                  <input value={concept} onChange={(event) => setConcept(event.target.value)} placeholder="例如：cup、chair、plant" onKeyDown={(event) => { if (event.key === "Enter") void segment(); }} />
                  <button disabled={busy || !project || !concept.trim()} onClick={segment}>识别</button>
                </div>
                <p className="action-guidance">{segmentHint}</p>

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
                <p className="action-guidance">{candidateHint}</p>
              </div>

              <div className="panel">
                <div className="panel-title"><span>2</span><strong>选择模型并生成</strong></div>
                {resultUrl ? <Suspense fallback={<div className="glb-viewer"><span className="viewer-message">正在加载 3D 引擎…</span></div>}><GlbViewer url={resultUrl} /></Suspense> : (
                  <div className="canonical-preview">{canonicalUrl ? <img src={canonicalUrl} alt="Canonical RGBA" /> : <div>确认对象后，这里会显示标准透明 RGBA。</div>}</div>
                )}
                {canonical && !resultUrl && <div className="asset-meta"><span>Canonical RGBA</span><strong>{(canonical.bytes / 1024).toFixed(0)} KiB</strong></div>}

                <div className="model-options">
                  {models.map((model) => (
                    <button key={model.id} className={`model-option ${model.id === selectedModel?.id ? "active" : ""}`} disabled={busy || model.status === "disabled"} onClick={() => { setModelId(model.id); resetOutput(); }}>
                      <div><strong>{model.name}</strong><span>{model.description}</span></div>
                      <div className="model-meta"><span>Warm ~{model.warm_seconds.toFixed(model.warm_seconds < 10 ? 1 : 0)}s</span><span>{model.output === "textured" ? "纹理" : "几何"}</span></div>
                    </button>
                  ))}
                  {!models.length ? <div className="workspace-recovery"><strong>未取得云端模型列表</strong><span>检查 Modal 连接后刷新状态。</span><button type="button" className="quiet-button" onClick={() => setSettingsOpen(true)}>打开设置</button></div> : null}
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
                {!job || !jobIsActive(job) ? <p className="action-guidance">{generationHint}</p> : null}

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
        ) : (
          <section className="connection-empty-state">
            <div className="empty-visual"><span>◇</span><span>◆</span><span>○</span></div>
            <span className="eyebrow">Ready when you are</span>
            <h2>连接 Modal，开始创建 3D 资产</h2>
            <p>账号凭据和运行时配置已经统一收纳到设置中心，工作台只专注创作。</p>
            <button className="primary-button" onClick={() => setSettingsOpen(true)}>打开设置</button>
          </section>
        )}
      </main>

      <SettingsPanel
        open={settingsOpen}
        onClose={closeSettings}
        controller={runtimeController}
      />
    </div>
  );
}

export default App;
