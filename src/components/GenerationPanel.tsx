import { lazy, Suspense } from "react";
import type {
  CanonicalAsset,
  GenerationJob,
  ModelProfile,
  ModelSpec,
  ProjectGeneration,
} from "../agent";

const GlbViewer = lazy(() => import("../GlbViewer"));

const activeStatuses = new Set<GenerationJob["status"]>(["running", "connection_required", "cancel_requested"]);

function activityLabel(status: GenerationJob["status"]) {
  if (status === "connection_required") return "等待云端连接";
  if (status === "cancel_requested") return "正在取消";
  return "云端生成中";
}

const generationStatusLabels: Record<ProjectGeneration["status"], string> = {
  draft: "等待处理",
  segmented: "旧项目",
  ready: "等待生成",
  submitting: "提交中",
  generating: "提交中",
  running: "生成中",
  connection_required: "等待连接",
  cancel_requested: "取消中",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已取消",
  expired: "已过期",
};

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
  busy: boolean;
  hint: string;
  onSelectModel: (modelId: string) => void;
  onGenerate: () => void;
  onCancel: () => void;
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
  busy,
  hint,
  onSelectModel,
  onGenerate,
  onCancel,
  onSelectGeneration,
  onExport,
  onOpenSettings,
}: GenerationPanelProps) {
  const activeJob = Boolean(job && activeStatuses.has(job.status));

  return (
    <section className="workspace-panel generation-panel" aria-labelledby="generation-title">
      <div className="panel-header">
        <div>
          <span className="panel-step">02 · 云端重构</span>
          <h2 id="generation-title">3D 模型</h2>
        </div>
        {resultUrl ? <span className={`asset-badge ${resultOutdated ? "outdated" : ""}`}>{resultOutdated ? "上一版模型" : "已保存"}</span> : null}
      </div>

      <div className="result-viewport">
        {resultUrl ? (
          <Suspense fallback={<div className="glb-viewer"><span className="viewer-message">加载 3D 引擎…</span></div>}>
            <GlbViewer url={resultUrl} />
          </Suspense>
        ) : (
          <div className="glb-viewer model-empty-state">
            <div className="empty-preview"><span>3D MODEL</span><strong>{canonical ? "选择模型并开始生成" : "图片处理完成后生成"}</strong></div>
          </div>
        )}
      </div>

      {resultOutdated ? (
        <div className="result-version-note"><strong>模型已保留</strong><span>当前图片有新修改；再次生成前，这里继续展示上一版结果。</span></div>
      ) : null}

      <div className="generation-library">
        <div className="section-label">
          <span>模型成果</span>
          <small>{generations.length ? `${generations.length} 个版本` : "生成后保存在这里"}</small>
        </div>
        {generations.length ? (
          <div className="generation-list">
            {generations.map((generation) => {
              const available = generation.status === "succeeded" && Boolean(generation.artifact_id);
              const active = generation.job_id === selectedGenerationJobId;
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
                    <strong>{modelName}</strong>
                    <small>{generationTime(generation.created_at)} · {generation.profile}</small>
                  </span>
                  <span className={`generation-status ${available ? "available" : ""}`}>
                    {generationStatusLabels[generation.status]}
                  </span>
                </button>
              );
            })}
          </div>
        ) : (
          <div className="generation-library-empty">这个项目还没有模型成果</div>
        )}
      </div>

      <div className="model-section">
        <div className="section-label"><span>生成模型</span>{selectedProfile ? <small>{selectedProfile.name}</small> : null}</div>
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
              <span className="model-meta"><small>~{model.warm_seconds.toFixed(model.warm_seconds < 10 ? 1 : 0)}s</small><small>{model.output === "textured" ? "纹理" : "几何"}</small></span>
            </button>
          ))}
          {!models.length ? (
            <div className="workspace-recovery">
              <span><strong>尚未连接 Modal</strong><small>本地处理不受影响</small></span>
              <button type="button" className="quiet-button" onClick={onOpenSettings}>连接</button>
            </div>
          ) : null}
        </div>
      </div>

      {activeJob && job ? (
        <div className="generation-actions">
          <button type="button" className="primary-button" disabled>{activityLabel(job.status)}…</button>
          <button type="button" className="danger-button" disabled={job.status === "cancel_requested"} onClick={onCancel}>{job.status === "cancel_requested" ? "确认中" : "取消"}</button>
        </div>
      ) : (
        <div className="panel-actions">
          <button type="button" className="primary-button" disabled={busy || !canonical || !selectedModel || selectedModel.status === "disabled"} onClick={onGenerate}>
            使用 {selectedModel?.name ?? "模型"} 生成
          </button>
          <span>{hint}</span>
        </div>
      )}

      {resultJob?.result ? (
        <div className="result-card">
          <span><strong>GLB 已就绪</strong><small>{(resultJob.result.artifact.bytes / 1024 / 1024).toFixed(2)} MiB</small></span>
          <span className="result-timing">
            {resultJob.result.timing.inference_s !== undefined ? <small>推理 {resultJob.result.timing.inference_s.toFixed(2)}s</small> : null}
            {resultJob.result.timing.load_s !== undefined ? <small>加载 {resultJob.result.timing.load_s.toFixed(2)}s</small> : null}
          </span>
          <button type="button" className="primary-button" onClick={onExport}>导出 GLB</button>
        </div>
      ) : null}
    </section>
  );
}
