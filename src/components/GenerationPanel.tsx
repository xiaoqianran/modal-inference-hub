import { lazy, Suspense } from "react";
import type { CanonicalAsset, GenerationJob, ModelProfile, ModelSpec } from "../agent";

const GlbViewer = lazy(() => import("../GlbViewer"));

const activeStatuses = new Set<GenerationJob["status"]>(["running", "connection_required", "cancel_requested"]);

function activityLabel(status: GenerationJob["status"]) {
  if (status === "connection_required") return "等待云端连接";
  if (status === "cancel_requested") return "正在取消";
  return "云端生成中";
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
  busy: boolean;
  hint: string;
  onSelectModel: (modelId: string) => void;
  onGenerate: () => void;
  onCancel: () => void;
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
  busy,
  hint,
  onSelectModel,
  onGenerate,
  onCancel,
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
