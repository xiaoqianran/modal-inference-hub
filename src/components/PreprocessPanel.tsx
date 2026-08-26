import {
  useRef,
  useState,
  type DragEvent as ReactDragEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import type {
  CanonicalAsset,
  ComponentState,
  ModelDownloadState,
  PreprocessResult,
  Project,
} from "../agent";

export type SelectionBox = {
  start: [number, number];
  current: [number, number];
  mode: "replace" | "add" | "subtract";
};

type PreprocessPanelProps = {
  project: Project | null;
  sourceUrl: string | null;
  matteUrl: string | null;
  canonical: CanonicalAsset | null;
  preprocessMeta: PreprocessResult["preprocess"] | null;
  modelDownload: ModelDownloadState | null;
  componentState: ComponentState | null;
  selectionBox: SelectionBox | null;
  canUndo: boolean;
  canRedo: boolean;
  agentReady: boolean;
  busy: boolean;
  hint: string;
  onChooseImage: (file: File | null) => void;
  onPreprocess: () => void;
  onToggleComponent: (componentId: string) => void;
  onSelectAll: () => void;
  onUndo: () => void;
  onRedo: () => void;
  onPointerDown: (event: ReactPointerEvent<SVGSVGElement>) => void;
  onPointerMove: (event: ReactPointerEvent<SVGSVGElement>) => void;
  onPointerUp: (event: ReactPointerEvent<SVGSVGElement>) => void;
  onPointerCancel: () => void;
};

export default function PreprocessPanel({
  project,
  sourceUrl,
  matteUrl,
  canonical,
  preprocessMeta,
  modelDownload,
  componentState,
  selectionBox,
  canUndo,
  canRedo,
  agentReady,
  busy,
  hint,
  onChooseImage,
  onPreprocess,
  onToggleComponent,
  onSelectAll,
  onUndo,
  onRedo,
  onPointerDown,
  onPointerMove,
  onPointerUp,
  onPointerCancel,
}: PreprocessPanelProps) {
  const selectedCount = componentState?.selected_component_ids.length ?? 0;
  const [showBounds, setShowBounds] = useState(true);
  const [showSourceContext, setShowSourceContext] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const dragDepthRef = useRef(0);

  const handleDragEnter = (event: ReactDragEvent<HTMLElement>) => {
    if (busy || !agentReady || !event.dataTransfer.types.includes("Files")) return;
    event.preventDefault();
    dragDepthRef.current += 1;
    setDragActive(true);
  };

  const handleDragLeave = (event: ReactDragEvent<HTMLElement>) => {
    if (!dragActive) return;
    event.preventDefault();
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
    if (dragDepthRef.current === 0) setDragActive(false);
  };

  const handleDrop = (event: ReactDragEvent<HTMLElement>) => {
    event.preventDefault();
    dragDepthRef.current = 0;
    setDragActive(false);
    if (busy || !agentReady) return;
    onChooseImage(event.dataTransfer.files?.[0] ?? null);
  };

  return (
    <section
      className={`workspace-panel preprocess-panel ${dragActive ? "drop-active" : ""}`}
      aria-labelledby="preprocess-title"
      onDragEnter={handleDragEnter}
      onDragOver={(event) => {
        if (busy || !agentReady) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = "copy";
      }}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {dragActive ? (
        <div className="drop-overlay" aria-hidden="true">
          <span>DROP IMAGE</span>
          <strong>{sourceUrl ? "释放以替换当前原图" : "释放以创建新项目"}</strong>
          <small>PNG · JPEG · WebP · 仍使用现有本地校验与 rembg 流程</small>
        </div>
      ) : null}
      <div className="panel-header">
        <div>
          <span className="panel-step">01–02 · LOCAL PREP</span>
          <h2 id="preprocess-title">图像与前景</h2>
          <p className="panel-description">原图保持本地；完成 rembg 后生成标准化 Canonical。</p>
        </div>
        <div className="panel-header-actions">
          {canonical ? <span className="asset-badge ready">CANONICAL READY</span> : null}
          <label className={`upload-button ${sourceUrl ? "secondary-upload" : ""} ${busy || !agentReady ? "disabled" : ""}`}>
            <input
              disabled={busy || !agentReady}
              type="file"
              accept="image/png,image/jpeg,image/webp"
              onChange={(event) => {
                onChooseImage(event.target.files?.[0] ?? null);
                event.currentTarget.value = "";
              }}
            />
            {sourceUrl ? "更换图片" : "导入图片"}
          </label>
        </div>
      </div>

      {modelDownload && modelDownload.status !== "ready" && (
        modelDownload.downloaded_bytes > 0 || modelDownload.status === "failed" || modelDownload.status === "verifying"
      ) ? (
        <div className="download-card">
          <div><span>{modelDownload.status === "verifying" ? "校验模型" : modelDownload.status === "failed" ? "下载中断" : "准备本地模型"}</span><strong>{Math.floor(modelDownload.progress * 100)}%</strong></div>
          <progress max={1} value={modelDownload.progress} />
          <small>{(modelDownload.downloaded_bytes / 1024 / 1024).toFixed(1)} / {(modelDownload.total_bytes / 1024 / 1024).toFixed(1)} MiB</small>
          {modelDownload.error ? <p className="inline-error">{modelDownload.error}</p> : null}
        </div>
      ) : null}

      <div className="preprocess-grid">
        <figure className="image-stage">
          <figcaption>原图</figcaption>
          {sourceUrl ? <img className="full-image" src={sourceUrl} alt="项目原图" /> : (
            <div className="empty-preview import-empty-state">
              <span>PNG · JPEG · WebP</span>
              <strong>拖入图片或点击“导入图片”</strong>
              <small>原图仅保存在本机，导入后自动开始 rembg</small>
            </div>
          )}
        </figure>
        <figure className="image-stage checker component-stage">
          <figcaption>前景选择</figcaption>
          {componentState ? (
            <div className="stage-tools" aria-label="前景预览工具">
              <button
                type="button"
                className={showBounds ? "active" : ""}
                aria-pressed={showBounds}
                onClick={() => setShowBounds((value) => !value)}
              >
                边界
              </button>
              <button
                type="button"
                className={showSourceContext ? "active" : ""}
                aria-pressed={showSourceContext}
                disabled={!sourceUrl}
                onClick={() => setShowSourceContext((value) => !value)}
              >
                原图参照
              </button>
            </div>
          ) : null}
          {matteUrl && componentState ? (
            <svg
              className={`component-overlay ${selectionBox ? "dragging" : ""}`}
              viewBox={`0 0 ${componentState.source_size[0]} ${componentState.source_size[1]}`}
              role="img"
              aria-label="当前前景，可拖框选择物体"
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={onPointerUp}
              onPointerCancel={onPointerCancel}
            >
              {showSourceContext && sourceUrl ? (
                <image
                  className="component-source-context"
                  href={sourceUrl}
                  width={componentState.source_size[0]}
                  height={componentState.source_size[1]}
                />
              ) : null}
              <image href={matteUrl} width={componentState.source_size[0]} height={componentState.source_size[1]} />
              {showBounds ? componentState.components.map((item, index) => {
                const [x1, y1, x2, y2] = item.bbox;
                const selected = componentState.selected_component_ids.includes(item.id);
                return (
                  <g key={item.id} className={`component-box ${selected ? "selected" : ""}`} onPointerDown={(event) => event.stopPropagation()} onClick={() => onToggleComponent(item.id)}>
                    <rect x={x1} y={y1} width={x2 - x1} height={y2 - y1} vectorEffect="non-scaling-stroke" />
                    <text x={x1 + 5} y={Math.max(y1 + 16, 18)}>{index + 1}</text>
                  </g>
                );
              }) : null}
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
          ) : matteUrl ? <img className="full-image" src={matteUrl} alt="本地抠图结果" /> : <div className="empty-preview"><span>LOCAL REMBG</span><strong>等待本地抠图</strong></div>}
        </figure>
      </div>

      {componentState ? (
        <div className="component-controls">
          <div className="selection-summary">
            <span><small>SELECTED</small><strong>{selectedCount}/{componentState.component_count}</strong></span>
            <span><small>FOREGROUND</small><strong>{preprocessMeta ? `${(preprocessMeta.foreground_ratio * 100).toFixed(1)}%` : "—"}</strong></span>
            <span><small>SOURCE</small><strong>{componentState.source_size[0]}×{componentState.source_size[1]}</strong></span>
            <span><small>CANONICAL</small><strong>{canonical ? "1024²" : "—"}</strong></span>
          </div>
          <div className="component-toolbar">
            <div><strong>{selectedCount}/{componentState.component_count} 个物体</strong><small>拖框选择 · Shift 追加 · Alt 移除</small></div>
            <div>
              <button type="button" className="quiet-button" disabled={busy || !canUndo} onClick={onUndo}>撤销</button>
              <button type="button" className="quiet-button" disabled={busy || !canRedo} onClick={onRedo}>重做</button>
              <button type="button" className="quiet-button" disabled={busy || selectedCount === componentState.component_count} onClick={onSelectAll}>全选</button>
            </div>
          </div>
          <div className="component-list">
            {componentState.components.map((item, index) => {
              const selected = componentState.selected_component_ids.includes(item.id);
              return (
                <label key={item.id} className={`component-item ${selected ? "selected" : ""}`}>
                  <input type="checkbox" checked={selected} disabled={busy || (selected && selectedCount === 1)} onChange={() => onToggleComponent(item.id)} />
                  <span>物体 {index + 1}</span>
                  <small>{(item.foreground_ratio * 100).toFixed(1)}%</small>
                </label>
              );
            })}
          </div>
          {componentState.ignored_component_count > 0 ? <small className="noise-note">已自动合并 {componentState.ignored_component_count} 个微小碎片</small> : null}
        </div>
      ) : null}

      <div className="panel-actions">
        <button type="button" className="primary-button" disabled={busy || !project || !agentReady} onClick={onPreprocess}>
          {busy ? "处理中…" : modelDownload?.status === "failed" ? modelDownload.resumable ? "续传并重试" : "准备 rembg" : canonical ? "重新运行 rembg" : "运行 rembg"}
        </button>
        <span>{hint}</span>
        {preprocessMeta ? <small className="process-meta">{preprocessMeta.provider.toUpperCase()} · {preprocessMeta.elapsed_ms.toFixed(0)} ms</small> : null}
      </div>
    </section>
  );
}
