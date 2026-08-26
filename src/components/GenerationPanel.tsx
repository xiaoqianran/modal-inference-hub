import { lazy, Suspense, useEffect, useState } from "react";
import type {
  CanonicalAsset,
  GenerationJob,
  ModelProfile,
  ModelSpec,
  Project,
  ProjectGeneration,
} from "../agent";
import { isJobActive } from "../generationState";

const GlbViewer = lazy(() => import("../GlbViewer"));

function activityLabel(status: GenerationJob["status"]) {
  if (status === "connection_required") return "等待云端连接";
  if (status === "cancel_requested") return "正在取消";
  return "云端生成中";
}

function elapsedLabel(startedAt: string, now: number) {
  const started = new Date(startedAt).getTime();
  if (!Number.isFinite(started)) return "—";
  const seconds = Math.max(0, Math.floor((now - started) / 1000));
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return minutes ? `${minutes}m ${remainder.toString().padStart(2, "0")}s` : `${seconds}s`;
}

const generationStatusLabels: Record<ProjectGeneration["status"], string> = {
  draft: "等待处理",
  segmented: "旧项目",
  ready: "等待生成",
  submitting: "提交中",
  submission_unknown: "提交待确认",
  generating: "提交中",
  running: "生成中",
  connection_required: "等待连接",
  cancel_requested: "取消中",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已取消",
  expired: "已过期",
};

function GenerationActivity({
  job,
  modelName,
  onCancel,
}: {
  job: GenerationJob;
  modelName: string;
  onCancel: () => void;
}) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const waitingConnection = job.status === "connection_required";
  const cancelling = job.status === "cancel_requested";
  return (
    <div className={`generation-activity ${waitingConnection ? "paused" : ""}`}>
      <div className="generation-activity-head">
        <span>
          <small>当前任务</small>
          <strong>{activityLabel(job.status)}</strong>
        </span>
        <span className="generation-elapsed">{elapsedLabel(job.created_at, now)}</span>
      </div>
      <div className="generation-activity-track"><i /></div>
      <div className="generation-activity-meta">
        <span><small>模型</small><strong>{modelName}</strong></span>
        <span><small>任务号</small><strong>{job.id.slice(0, 8)}</strong></span>
        <span><small>状态</small><strong>{generationStatusLabels[job.status]}</strong></span>
      </div>
      <div className="generation-activity-steps" aria-label="生成状态">
        <span className="complete"><i />任务已提交</span>
        <span className={waitingConnection || cancelling ? "waiting" : "current"}><i />{waitingConnection ? "等待云端连接" : cancelling ? "等待取消确认" : "云端处理中"}</span>
        <span><i />模型回传</span>
      </div>
      <button type="button" className="danger-button" disabled={cancelling} onClick={onCancel}>
        {cancelling ? "取消确认中" : "取消任务"}
      </button>
    </div>
  );
}

function generationTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

type GenerationPanelProps = {
  canonical: CanonicalAsset | null;
  resultUrl: string | null;
  resultOutdated: boolean;
  models: ModelSpec[];
  selectedModel?: ModelSpec;
  selectedProfile?: ModelProfile;
  job: GenerationJob | null;
  resultJob: GenerationJob | null;
  generations: ProjectGeneration[];
  selectedGenerationJobId: string | null;
  projectStatus: Project["status"] | null;
  busy: boolean;
  hint: string;
  onSelectModel: (modelId: string) => void;
  onGenerate: () => void;
  onCancel: () => void;
  onAbandonUnknown: () => void;
  onSelectGeneration: (generation: ProjectGeneration) => void;
  onExport: () => void;
  onOpenSettings: () => void;
};

export default function GenerationPanel({
  canonical,
  resultUrl,
  resultOutdated,
  models,
  selectedModel,
  selectedProfile,
  job,
  resultJob,
  generations,
  selectedGenerationJobId,
  projectStatus,
  busy,
  hint,
  onSelectModel,
  onGenerate,
  onCancel,
  onAbandonUnknown,
  onSelectGeneration,
  onExport,
  onOpenSettings,
}: GenerationPanelProps) {
  const activeJob = isJobActive(job);
  const submissionUnknown = projectStatus === "submission_unknown";
  const latestAvailableGeneration = generations.find((generation) =>
    generation.status === "succeeded" && Boolean(generation.artifact_id)
  ) ?? null;
  const latestAvailableJobId = latestAvailableGeneration?.job_id ?? null;
  const selectedGeneration = generations.find(
    (generation) => generation.job_id === selectedGenerationJobId
  ) ?? null;
  const comparingHistory = Boolean(
    selectedGeneration && latestAvailableGeneration
    && selectedGeneration.job_id !== latestAvailableGeneration.job_id
  );

  return (
    <section className="workspace-panel generation-panel" aria-labelledby="generation-title">
      <div className="panel-header">
        <div>
          <span className="panel-step">第二步 · 云端生成</span>
          <h2 id="generation-title">生成 3D 模型</h2>
          <p className="panel-description">只在生成时上传标准化前景；每次结果都会保存为可切换的版本。</p>
        </div>
        {resultUrl ? (
          <span className={`asset-badge ${resultOutdated ? "outdated" : "ready"}`}>
            {resultOutdated ? "上一版本" : "模型已就绪"}
          </span>
        ) : null}
      </div>

      <div className="result-viewport">
        {resultUrl ? (
          <Suspense fallback={<div className="glb-viewer"><span className="viewer-message">加载 3D 引擎…</span></div>}>
            <GlbViewer url={resultUrl} />
          </Suspense>
        ) : (
          <div className="glb-viewer model-empty-state">
            <div className="empty-preview"><span>3D 预览</span><strong>{canonical ? "选择模型，开始生成" : "完成左侧前景处理后，在这里生成 3D"}</strong></div>
          </div>
        )}
      </div>

      {resultOutdated ? (
        <div className="result-version-note"><strong>正在查看上一版</strong><span>当前前景已经变化；新生成完成前仍保留这一版供比较。</span></div>
      ) : null}

      <div className="model-section">
        <div className="section-label">
          <span>选择生成模型</span>
          {selectedProfile ? <small>{selectedProfile.name}</small> : null}
        </div>
        <div className="model-options">
          {models.map((model) => (
            <button
              type="button"
              key={model.id}
              className={`model-option ${model.id === selectedModel?.id ? "active" : ""}`}
              disabled={busy || model.status === "disabled"}
              onClick={() => onSelectModel(model.id)}
            >
              <span><strong>{model.name}</strong><small>{model.description}</small></span>
              <span className="model-meta"><small>约 {model.warm_seconds.toFixed(model.warm_seconds < 10 ? 1 : 0)}s</small><small>{model.output === "textured" ? "带纹理" : "仅几何"}</small></span>
            </button>
          ))}
          {!models.length ? (
            <div className="workspace-recovery">
              <span><strong>尚未连接 Modal</strong><small>本地图片处理仍可继续</small></span>
              <button type="button" className="quiet-button" onClick={onOpenSettings}>连接云端</button>
            </div>
          ) : null}
        </div>
      </div>

      {submissionUnknown ? (
        <div className="generation-actions generation-warning">
          <span>
            <strong>上次提交结果无法确认</strong>
            <small>为避免重复计费，已暂停自动重提。解锁后再次生成可能产生重复云任务。</small>
          </span>
          <button type="button" className="danger-button" disabled={busy} onClick={onAbandonUnknown}>
            放弃待确认并解锁
          </button>
        </div>
      ) : activeJob && job ? (
        <GenerationActivity job={job} modelName={selectedModel?.name ?? job.model} onCancel={onCancel} />
      ) : (
        <div className="panel-actions generation-primary-action">
          <button type="button" className="primary-button" disabled={busy || !canonical || !selectedModel || selectedModel.status === "disabled"} onClick={onGenerate}>
            用 {selectedModel?.name ?? "模型"} 生成 3D
          </button>
          <span>{hint}</span>
        </div>
      )}

      {resultJob?.result ? (
        <div className="result-card result-delivery-card">
          <span><strong>模型已生成</strong><small>{(resultJob.result.artifact.bytes / 1024 / 1024).toFixed(2)} MiB</small></span>
          <span className="result-timing">
            {resultJob.result.timing.inference_s !== undefined ? <small>推理 {resultJob.result.timing.inference_s.toFixed(2)}s</small> : null}
            {resultJob.result.timing.load_s !== undefined ? <small>加载 {resultJob.result.timing.load_s.toFixed(2)}s</small> : null}
          </span>
          <button type="button" className="primary-button" onClick={onExport}>导出模型</button>
        </div>
      ) : null}

      <div className="generation-library">
        <div className="section-label">
          <span>版本历史</span>
          <small>{generations.length ? `${generations.length} 个模型版本` : "生成后自动保存在这里"}</small>
        </div>
        {comparingHistory && selectedGeneration && latestAvailableGeneration ? (
          <div className="version-compare-strip">
            <span>
              <small>版本对比</small>
              <strong>正在查看历史版本</strong>
            </span>
            <span className="version-compare-meta">
              <small>{selectedGeneration.canonical_sha256 === latestAvailableGeneration.canonical_sha256 ? "同一前景" : "前景版本不同"}</small>
              {selectedGeneration.artifact_bytes && latestAvailableGeneration.artifact_bytes ? (
                <small>
                  体积差 {(
                    (selectedGeneration.artifact_bytes - latestAvailableGeneration.artifact_bytes)
                    / 1024 / 1024
                  ).toFixed(2)} MiB
                </small>
              ) : null}
            </span>
            <button type="button" className="quiet-button" disabled={busy} onClick={() => onSelectGeneration(latestAvailableGeneration)}>
              查看最新
            </button>
          </div>
        ) : null}
        {generations.length ? (
          <div className="generation-list">
            {generations.map((generation) => {
              const available = generation.status === "succeeded" && Boolean(generation.artifact_id);
              const active = generation.job_id === selectedGenerationJobId;
              const latest = generation.job_id === latestAvailableJobId;
              const modelName = models.find((model) => model.id === generation.model)?.name ?? generation.model;
              return (
                <button
                  type="button"
                  key={generation.id}
                  className={`generation-item ${active ? "active" : ""}`}
                  disabled={busy || !available}
                  aria-pressed={active}
                  onClick={() => onSelectGeneration(generation)}
                >
                  <span>
                    <strong>{modelName}{latest ? <em>最新</em> : null}</strong>
                    <small>
                      {generationTime(generation.created_at)} · {generation.profile}
                      {generation.artifact_bytes ? ` · ${(generation.artifact_bytes / 1024 / 1024).toFixed(2)} MiB` : ""}
                    </small>
                  </span>
                  <span className={`generation-status ${available ? "available" : ""} ${active ? "viewing" : ""}`}>
                    {active ? "正在查看" : generationStatusLabels[generation.status]}
                  </span>
                </button>
              );
            })}
          </div>
        ) : (
          <div className="generation-library-empty">暂无版本 · 第一次生成完成后会出现在这里</div>
        )}
      </div>
    </section>
  );
}
