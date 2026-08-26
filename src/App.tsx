import { useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import "./App.css";
import {
  abandonUnknownProjectGeneration,
  createProject,
  deleteProject,
  getJob,
  getProject,
  getProjectComponents,
  getPreprocessStatus,
  jobArtifactBlob,
  listProjectGenerations,
  listProjects,
  preprocessProject,
  projectSelectionBlob,
  projectSourceBlob,
  selectProjectComponents,
  type CanonicalAsset,
  type ComponentState,
  type ModelDownloadState,
  type PreprocessResult,
  type Project,
  type ProjectGeneration,
} from "./agent";
import SettingsPanel from "./SettingsPanel";
import Gallery from "./Gallery";
import { useRuntimeController } from "./useRuntimeController";
import AppHeader from "./components/AppHeader";
import CommandFeedback from "./components/CommandFeedback";
import GenerationPanel from "./components/GenerationPanel";
import GenerationReviewDialog from "./components/GenerationReviewDialog";
import PreprocessPanel, { type SelectionBox } from "./components/PreprocessPanel";
import ProjectSidebar from "./components/ProjectSidebar";
import WorkflowProgress from "./components/WorkflowProgress";
import { useCommandFeedback } from "./hooks/useCommandFeedback";
import { useObjectUrl } from "./hooks/useObjectUrl";
import { isProjectGenerationActive } from "./generationState";
import { workflowShortcutAction } from "./workflowShortcuts";

import { useGenerationJob, jobIsActive } from "./hooks/useGenerationJob";

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
  const [view, setView] = useState<"workbench" | "gallery">("workbench");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [generationReviewOpen, setGenerationReviewOpen] = useState(false);
  const [modelId, setModelId] = useState("");
  const [project, setProject] = useState<Project | null>(null);
  const [recentProjects, setRecentProjects] = useState<Project[]>([]);
  const [generations, setGenerations] = useState<ProjectGeneration[]>([]);
  const [selectedGenerationJobId, setSelectedGenerationJobId] = useState<string | null>(null);
  const [sourceUrl, replaceSourceUrl] = useObjectUrl();
  const [matteUrl, replaceMatteUrl] = useObjectUrl();
  const [canonical, setCanonical] = useState<CanonicalAsset | null>(null);
  const [preprocessMeta, setPreprocessMeta] = useState<PreprocessResult["preprocess"] | null>(null);
  const [modelDownload, setModelDownload] = useState<ModelDownloadState | null>(null);
  const [componentState, setComponentState] = useState<ComponentState | null>(null);
  const [selectionBox, setSelectionBox] = useState<SelectionBox | null>(null);
  const [selectionHistory, setSelectionHistory] = useState<string[][]>([]);
  const [selectionFuture, setSelectionFuture] = useState<string[][]>([]);
  const [resultUrl, replaceResultUrl] = useObjectUrl();
  const [workflowMessage, setWorkflowMessage] = useState(
    "选择图片后，在本机完成 rembg 抠图和 Canonical 规范化。",
  );
  const { feedback, notify, dismiss: dismissFeedback } = useCommandFeedback();
  const [busy, setBusy] = useState(false);
  const restoredAgent = useRef<number | null>(null);
  const selectionRequestRef = useRef(false);
  const projectRequestRef = useRef(0);
  const prepareSectionRef = useRef<HTMLDivElement | null>(null);
  const generationSectionRef = useRef<HTMLDivElement | null>(null);
  const shortcutRef = useRef({ enabled: false, undo: () => undefined, redo: () => undefined });
  const workflowShortcutRef = useRef<{
    enabled: boolean;
    goPrepare: () => void;
    goGenerate: () => void;
    generate: () => void;
    openSettings: () => void;
  }>({
    enabled: false,
    goPrepare: () => undefined,
    goGenerate: () => undefined,
    generate: () => undefined,
    openSettings: () => undefined,
  });

  const selectedModel = modelId
    ? models.find((model) => model.id === modelId)
    : models.find((model) => model.status !== "disabled") ?? models[0];
  const selectedProfile = selectedModel?.profiles[0];

  const refreshRecent = useCallback(async () => {
    if (!agent?.running) return;
    try {
      setRecentProjects(await listProjects(agent));
    } catch {
      // Recent projects are auxiliary and must not block the workspace.
    }
  }, [agent]);

  const refreshGenerations = useCallback(async (projectId: string) => {
    if (!agent?.running) return;
    try {
      setGenerations(await listProjectGenerations(agent, projectId));
    } catch {
      // Generation history is auxiliary; the active job remains authoritative.
    }
  }, [agent]);

  const handleResultReady = useCallback((jobId: string) => {
    setSelectedGenerationJobId(jobId);
    notify({
      tone: "success",
      title: "3D 生成完成",
      detail: "GLB 已回传到本机，可以检查视角、版本并导出。",
      action: {
        label: "查看结果",
        run: () => generationSectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }),
      },
    }, 5_200);
  }, [notify]);

  const {
    job, resultJob, resultCanonicalSha, submitting, resetOutput, pollJob, restoreJob,
    restoreResult, generate, cancel, downloadResult,
  } = useGenerationJob({
    agent, project, canonical, selectedModel, selectedProfile, modalConnected,
    replaceResultUrl, setProject, refreshRecent, refreshGenerations,
    onResultReady: handleResultReady, setWorkflowMessage,
  });

  const closeGenerationReview = useCallback(() => setGenerationReviewOpen(false), []);
  const confirmGenerationReview = useCallback(() => {
    setGenerationReviewOpen(false);
    void generate();
  }, [generate]);

  const restoreProject = useCallback(async (projectId: string) => {
    if (!agent?.running) return;
    const requestId = ++projectRequestRef.current;
    setBusy(true);
    try {
      const value = await getProject(agent, projectId);
      const savedCanonical = canonicalFromProject(value);
      const previewPromise = savedCanonical
        ? Promise.all([
            projectSelectionBlob(agent, value.id).catch(() => null),
            getProjectComponents(agent, value.id).catch(() => null),
          ])
        : Promise.resolve([null, null] as const);
      const restoredJobPromise = value.job_id
        ? getJob(agent, value.job_id).catch(() => null)
        : Promise.resolve(null);
      const [source, preview, projectGenerations, currentJob] = await Promise.all([
        projectSourceBlob(agent, value.id),
        previewPromise,
        listProjectGenerations(agent, value.id),
        restoredJobPromise,
      ]);
      const selectedGeneration = projectGenerations.find(
        (item) => item.status === "succeeded" && Boolean(item.artifact_id),
      );
      const displayedJob = selectedGeneration
        ? selectedGeneration.job_id === currentJob?.id
          ? currentJob
          : await getJob(agent, selectedGeneration.job_id).catch(() => null)
        : null;
      const displayedArtifact = displayedJob?.result
        ? await jobArtifactBlob(agent, displayedJob.id).catch(() => null)
        : null;
      if (requestId !== projectRequestRef.current) return;

      setProject(value);
      setCanonical(savedCanonical);
      setPreprocessMeta(null);
      setComponentState(preview[1]?.component_state ?? null);
      setSelectionHistory([]);
      setSelectionFuture([]);
      setGenerations(projectGenerations);
      setSelectedGenerationJobId(selectedGeneration?.job_id ?? null);
      replaceSourceUrl(source);
      replaceMatteUrl(preview[0]);
      restoreJob(currentJob, value.id);
      restoreResult(displayedJob, selectedGeneration?.canonical_sha256);
      replaceResultUrl(displayedArtifact);
      if (value.model) setModelId(value.model);
      if (currentJob && jobIsActive(currentJob)) {
        void pollJob(currentJob.id, value.id, value.canonical_sha256);
      }
      setWorkflowMessage(
        displayedJob?.result
          ? `项目与 ${projectGenerations.length} 个模型成果已恢复。`
          : value.canonical_id
            ? "本地 Canonical 已恢复，可以继续生成。"
            : "原图已恢复，可以执行本地抠图。",
      );
    } catch (error) {
      if (requestId === projectRequestRef.current) {
        setWorkflowMessage(error instanceof Error ? error.message : String(error));
      }
    } finally {
      if (requestId === projectRequestRef.current) setBusy(false);
    }
  }, [agent, pollJob, replaceMatteUrl, replaceResultUrl, replaceSourceUrl, restoreJob, restoreResult]);

  useEffect(() => {
    if (!modelId && models.length) {
      setModelId(models.find((model) => model.status !== "disabled")?.id ?? models[0].id);
    }
  }, [modelId, models]);

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

  useEffect(() => {
    if (runtimeController.initialized && !agent?.running) {
      setWorkflowMessage(`本地服务不可用：${runtimeController.agentMessage}`);
    }
  }, [agent?.running, runtimeController.agentMessage, runtimeController.initialized]);

  async function runLocalPreprocess(target: Project) {
    if (!agent?.running) return;
    const cached = runtimeController.runtime?.preprocessing.model_downloaded;
    setWorkflowMessage(
      cached
        ? "正在本机执行 birefnet-general-lite 抠图…"
        : "首次使用：正在准备 birefnet-general-lite 本地模型…",
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
          setWorkflowMessage(`正在下载 birefnet-general-lite · ${percent}%`);
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
      setSelectionHistory([]);
      setSelectionFuture([]);
      const matte = await projectSelectionBlob(agent, target.id);
      replaceMatteUrl(matte);
      setWorkflowMessage(
        `本地抠图完成 · ${value.preprocess.provider.toUpperCase()} · ${value.component_state.component_count} 个可选前景 · ${value.preprocess.elapsed_ms.toFixed(0)} ms`,
      );
      notify({
        tone: "success",
        title: "前景准备完成",
        detail: `${value.component_state.component_count} 个可选前景 · ${value.preprocess.elapsed_ms.toFixed(0)} ms`,
      });
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
    projectRequestRef.current += 1;
    setBusy(true);
    let createdProject: Project | null = null;
    try {
      const value = await createProject(agent, file);
      createdProject = value;
      setProject(value);
      setCanonical(null);
      setPreprocessMeta(null);
      setComponentState(null);
      setSelectionHistory([]);
      setSelectionFuture([]);
      setModelDownload(null);
      setGenerations([]);
      setSelectedGenerationJobId(null);
      resetOutput();
      replaceSourceUrl(file);
      replaceMatteUrl(null);
      setWorkflowMessage("原图已保存在本机，正在自动开始 rembg 预处理…");
      notify({
        tone: "info",
        title: "项目已创建",
        detail: `${file.name} · 原图仅保存在本机`,
      });
      await refreshRecent();
      await runLocalPreprocess(value);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      const retryProject = createdProject;
      setWorkflowMessage(retryProject
        ? `${message}；项目与原图已保留，可直接重试本地抠图。`
        : `图片导入失败：${message}`);
      notify({
        tone: "error",
        title: retryProject ? "本地预处理失败" : "图片导入失败",
        detail: message,
        action: retryProject ? {
          label: "重试 rembg",
          run: () => { void retryPreprocessTarget(retryProject); },
        } : undefined,
      }, 5_500);
    } finally {
      setBusy(false);
    }
  }

  async function retryPreprocessTarget(target: Project) {
    if (!agent?.running || busy) return;
    setBusy(true);
    try {
      await runLocalPreprocess(target);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setWorkflowMessage(message);
      notify({
        tone: "error",
        title: "本地预处理失败",
        detail: message,
        action: { label: "再次重试", run: () => { void retryPreprocessTarget(target); } },
      }, 5_500);
    } finally {
      setBusy(false);
    }
  }

  async function preprocess() {
    if (!project) return;
    await retryPreprocessTarget(project);
  }

  async function applyComponentSelection(
    selectedIds: string[],
    options: { recordHistory?: boolean } = {},
  ) {
    if (selectionRequestRef.current || !agent?.running || !project || !componentState || selectedIds.length === 0) return false;
    if (job && jobIsActive(job)) {
      setWorkflowMessage("远程生成任务活动期间不能修改前景选择。");
      return false;
    }
    const previousSelection = [...componentState.selected_component_ids];
    const normalized = componentState.components
      .filter((item) => selectedIds.includes(item.id))
      .map((item) => item.id);
    if (normalized.length === 0) {
      setWorkflowMessage("至少保留一个前景组件。");
      return false;
    }
    if (
      normalized.length === previousSelection.length
      && normalized.every((item, index) => item === previousSelection[index])
    ) {
      return true;
    }

    selectionRequestRef.current = true;
    setBusy(true);
    try {
      const value = await selectProjectComponents(agent, project.id, normalized);
      setProject(value.project);
      setCanonical(value.canonical);
      setComponentState(value.component_state);
      if (options.recordHistory !== false) {
        setSelectionHistory((current) => [...current.slice(-49), previousSelection]);
        setSelectionFuture([]);
      }
      const selectionBlob = await projectSelectionBlob(agent, project.id);
      replaceMatteUrl(selectionBlob);
      const count = value.component_state.selected_component_ids.length;
      const elapsed = value.component_state.selection_elapsed_ms;
      setWorkflowMessage(
        `已保留 ${count}/${value.component_state.component_count} 个前景${elapsed !== undefined ? ` · ${elapsed.toFixed(0)} ms` : ""}`,
      );
      await refreshRecent();
      return true;
    } catch (error) {
      setWorkflowMessage(error instanceof Error ? error.message : String(error));
      return false;
    } finally {
      selectionRequestRef.current = false;
      setBusy(false);
    }
  }

  async function undoSelection() {
    if (busy || selectionRequestRef.current || !componentState || selectionHistory.length === 0) return;
    const target = selectionHistory[selectionHistory.length - 1];
    const current = [...componentState.selected_component_ids];
    if (await applyComponentSelection(target, { recordHistory: false })) {
      setSelectionHistory((history) => history.slice(0, -1));
      setSelectionFuture((future) => [...future.slice(-49), current]);
    }
  }

  async function redoSelection() {
    if (busy || selectionRequestRef.current || !componentState || selectionFuture.length === 0) return;
    const target = selectionFuture[selectionFuture.length - 1];
    const current = [...componentState.selected_component_ids];
    if (await applyComponentSelection(target, { recordHistory: false })) {
      setSelectionFuture((future) => future.slice(0, -1));
      setSelectionHistory((history) => [...history.slice(-49), current]);
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
    const mode = event.altKey ? "subtract" : event.shiftKey ? "add" : "replace";
    setSelectionBox({ start: point, current: point, mode });
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
    const hitIds = new Set(hit.map((item) => item.id));
    const selected = new Set(componentState.selected_component_ids);
    if (selectionBox.mode === "replace") {
      void applyComponentSelection(componentState.components.filter((item) => hitIds.has(item.id)).map((item) => item.id));
      return;
    }
    if (selectionBox.mode === "add") {
      for (const item of hitIds) selected.add(item);
    } else {
      for (const item of hitIds) selected.delete(item);
      if (selected.size === 0) {
        setWorkflowMessage("Alt 框选不能移除全部前景；至少保留一个物体。");
        return;
      }
    }
    void applyComponentSelection(
      componentState.components.filter((item) => selected.has(item.id)).map((item) => item.id),
    );
  }

  shortcutRef.current = {
    enabled: view === "workbench" && Boolean(componentState) && !settingsOpen && !busy,
    undo: () => { void undoSelection(); },
    redo: () => { void redoSelection(); },
  };

  useEffect(() => {
    function handleSelectionHistoryShortcut(event: KeyboardEvent) {
      if (!shortcutRef.current.enabled) return;
      const target = event.target as HTMLElement | null;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable)) return;
      if (!(event.ctrlKey || event.metaKey) || event.altKey || event.key.toLowerCase() !== "z") return;
      event.preventDefault();
      if (event.shiftKey) shortcutRef.current.redo();
      else shortcutRef.current.undo();
    }
    window.addEventListener("keydown", handleSelectionHistoryShortcut);
    return () => window.removeEventListener("keydown", handleSelectionHistoryShortcut);
  }, []);

  async function removeProject(value: Project) {
    if (!agent?.running) return;
    if (isProjectGenerationActive(value.status)) {
      setWorkflowMessage("该项目仍有远程任务活动，请先等待或取消。");
      notify({
        tone: "warning",
        title: "项目正在生成",
        detail: "活动任务结束或取消前不能删除本地项目。",
        action: {
          label: "查看任务",
          run: () => generationSectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }),
        },
      });
      return;
    }
    if (!window.confirm(`确认删除“${value.title}”？本地项目文件会一并移除。`)) return;
    try {
      await deleteProject(agent, value.id);
      if (project?.id === value.id) {
        projectRequestRef.current += 1;
        setProject(null);
        setCanonical(null);
        setPreprocessMeta(null);
        setComponentState(null);
        setSelectionHistory([]);
        setSelectionFuture([]);
        setGenerations([]);
        setSelectedGenerationJobId(null);
        resetOutput();
        replaceSourceUrl(null);
        replaceMatteUrl(null);
      }
      await refreshRecent();
      notify({ tone: "success", title: "项目已删除", detail: value.title });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setWorkflowMessage(message);
      notify({ tone: "error", title: "项目删除失败", detail: message }, 5_500);
    }
  }

  async function abandonUnknownGeneration() {
    if (!agent?.running || !project || project.status !== "submission_unknown") return;
    if (!window.confirm(
      "上次远端提交结果无法确认。解锁后再次生成可能产生重复云任务和重复计费。确认放弃待确认状态？",
    )) return;

    setBusy(true);
    try {
      const updated = await abandonUnknownProjectGeneration(agent, project.id);
      setProject(updated);
      await refreshRecent();
      setWorkflowMessage("已放弃待确认状态。再次生成将创建新的远端任务。");
      notify({
        tone: "warning",
        title: "已解除提交锁定",
        detail: "再次生成会创建新的远端任务，请留意潜在重复任务。",
      }, 5_000);
    } catch (error) {
      setWorkflowMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function selectGeneration(value: ProjectGeneration) {
    if (!agent?.running || !project || value.status !== "succeeded" || !value.artifact_id) return;
    const requestId = projectRequestRef.current;
    setBusy(true);
    try {
      const restored = await getJob(agent, value.job_id);
      if (!restored.result) throw new Error("该模型成果尚无可用 GLB");
      const artifact = await jobArtifactBlob(agent, value.job_id);
      if (requestId !== projectRequestRef.current) return;
      restoreResult(restored, value.canonical_sha256);
      replaceResultUrl(artifact);
      setSelectedGenerationJobId(value.job_id);
      setWorkflowMessage("已切换到选中的模型成果。");
    } catch (error) {
      setWorkflowMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }


  const resultOutdated = Boolean(
    resultUrl && (
      (job && jobIsActive(job) && !job.result)
      || (resultCanonicalSha && canonical?.sha256 !== resultCanonicalSha)
    ),
  );
  const stage = resultUrl && !resultOutdated ? 3 : canonical ? 2 : project ? 1 : 0;
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
  const navigateWorkflow = (target: "prepare" | "generate") => {
    const element = target === "prepare" ? prepareSectionRef.current : generationSectionRef.current;
    element?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const requestGeneration = () => {
    if (!canonical) {
      navigateWorkflow("prepare");
      setWorkflowMessage("先完成本地 rembg 与 Canonical，再开始 3D 重构。");
      notify({
        tone: "warning",
        title: "还不能开始 3D 重构",
        detail: "先完成本地 rembg 与 Canonical。",
      });
      return;
    }
    if (!modalConnected) {
      notify({
        tone: "warning",
        title: "Modal Cloud 尚未连接",
        detail: "已打开控制中心，请先完成云端凭据连接。",
      });
      setSettingsOpen(true);
      return;
    }
    if (!selectedModel || !selectedProfile || (job && jobIsActive(job)) || busy || submitting) return;
    setGenerationReviewOpen(true);
  };

  workflowShortcutRef.current = {
    enabled: view === "workbench" && !settingsOpen && !generationReviewOpen && !busy && !submitting,
    goPrepare: () => navigateWorkflow("prepare"),
    goGenerate: () => navigateWorkflow("generate"),
    openSettings: () => {
      setGenerationReviewOpen(false);
      setSettingsOpen(true);
    },
    generate: requestGeneration,
  };

  useEffect(() => {
    function handleWorkflowShortcut(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable)) return;
      const action = workflowShortcutAction(event);
      if (!action) return;
      if (action === "settings") {
        event.preventDefault();
        workflowShortcutRef.current.openSettings();
        return;
      }
      if (!workflowShortcutRef.current.enabled) return;
      event.preventDefault();
      if (action === "prepare") workflowShortcutRef.current.goPrepare();
      else if (action === "generate") workflowShortcutRef.current.goGenerate();
      else workflowShortcutRef.current.generate();
    }
    window.addEventListener("keydown", handleWorkflowShortcut);
    return () => window.removeEventListener("keydown", handleWorkflowShortcut);
  }, []);

  return (
    <div className={`app-shell ${view === "gallery" ? "gallery-mode" : ""}`}>
      {view === "workbench" ? (
        <ProjectSidebar
          agent={agent}
          projects={recentProjects}
          activeProjectId={project?.id}
          busy={busy}
          onSelect={(projectId) => { void restoreProject(projectId); }}
          onDelete={(value) => { void removeProject(value); }}
        />
      ) : null}

      <div className="app-main">
        <AppHeader
          projectTitle={project?.title}
          view={view}
          agentReady={Boolean(agent?.running)}
          modalConnected={modalConnected}
          busy={busy}
          onChangeView={(nextView) => {
            if (nextView === "gallery") setGenerationReviewOpen(false);
            setView(nextView);
          }}
          onOpenSettings={() => setSettingsOpen(true)}
        />

        {view === "gallery" ? (
          <Gallery
            agent={agent}
            models={models}
            onLibraryChanged={refreshRecent}
            onOpenProject={(projectId) => {
              setView("workbench");
              void restoreProject(projectId);
            }}
          />
        ) : (
          <>
            <main className="workspace">
              <WorkflowProgress
                stage={stage}
                message={workflowMessage}
                generationActive={Boolean(job && jobIsActive(job))}
                generationCount={generations.length}
                modelName={selectedModel?.name}
                onNavigate={navigateWorkflow}
              />
              <div className="workspace-columns">
                <div ref={prepareSectionRef} className="workspace-lane workspace-lane-primary">
                  <PreprocessPanel
                    project={project}
                    sourceUrl={sourceUrl}
                    matteUrl={matteUrl}
                    canonical={canonical}
                    preprocessMeta={preprocessMeta}
                    modelDownload={modelDownload}
                    componentState={componentState}
                    selectionBox={selectionBox}
                    canUndo={selectionHistory.length > 0}
                    canRedo={selectionFuture.length > 0}
                    agentReady={Boolean(agent?.running)}
                    busy={busy}
                    hint={preprocessHint}
                    onChooseImage={(file) => { void chooseImage(file); }}
                    onPreprocess={() => { void preprocess(); }}
                    onToggleComponent={toggleComponent}
                    onSelectAll={selectAllComponents}
                    onUndo={() => { void undoSelection(); }}
                    onRedo={() => { void redoSelection(); }}
                    onPointerDown={beginBoxSelection}
                    onPointerMove={moveBoxSelection}
                    onPointerUp={finishBoxSelection}
                    onPointerCancel={() => setSelectionBox(null)}
                  />
                </div>
                <div ref={generationSectionRef} className="workspace-lane workspace-lane-secondary">
                  <GenerationPanel
                    canonical={canonical}
                    resultUrl={resultUrl}
                    resultOutdated={resultOutdated}
                    models={models}
                    selectedModel={selectedModel}
                    selectedProfile={selectedProfile}
                    job={job}
                    resultJob={resultJob}
                    generations={generations}
                    selectedGenerationJobId={selectedGenerationJobId}
                    projectStatus={project?.status ?? null}
                    busy={busy || submitting}
                    hint={generationHint}
                    onSelectModel={(nextModelId) => { setModelId(nextModelId); }}
                    onGenerate={requestGeneration}
                    onCancel={() => { void cancel(); }}
                    onAbandonUnknown={() => { void abandonUnknownGeneration(); }}
                    onSelectGeneration={(value) => { void selectGeneration(value); }}
                    onExport={() => { void downloadResult(project?.title); }}
                    onOpenSettings={() => setSettingsOpen(true)}
                  />
                </div>
              </div>
            </main>

            <GenerationReviewDialog
              open={generationReviewOpen}
              project={project}
              canonical={canonical}
              model={selectedModel}
              profile={selectedProfile}
              selectedComponents={componentState?.selected_component_ids.length ?? 0}
              componentCount={componentState?.component_count ?? 0}
              busy={busy || submitting}
              onCancel={closeGenerationReview}
              onConfirm={confirmGenerationReview}
            />
          </>
        )}
      </div>

      <CommandFeedback feedback={feedback} onDismiss={dismissFeedback} />
      <SettingsPanel open={settingsOpen} onClose={() => setSettingsOpen(false)} controller={runtimeController} />
    </div>
  );
}

export default App;
