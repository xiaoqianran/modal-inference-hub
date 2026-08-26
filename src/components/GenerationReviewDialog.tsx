import { useEffect, useRef } from "react";
import type { CanonicalAsset, ModelProfile, ModelSpec, Project } from "../agent";

type GenerationReviewDialogProps = {
  open: boolean;
  project: Project | null;
  canonical: CanonicalAsset | null;
  model?: ModelSpec;
  profile?: ModelProfile;
  selectedComponents: number;
  componentCount: number;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
};

function fileSize(bytes: number) {
  return bytes >= 1024 * 1024
    ? `${(bytes / 1024 / 1024).toFixed(2)} MiB`
    : `${Math.max(1, Math.round(bytes / 1024))} KiB`;
}

export default function GenerationReviewDialog({
  open,
  project,
  canonical,
  model,
  profile,
  selectedComponents,
  componentCount,
  busy,
  onCancel,
  onConfirm,
}: GenerationReviewDialogProps) {
  const dialogRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (!open) return;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const dialog = dialogRef.current;
    const focusable = () => Array.from(
      dialog?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)") ?? [],
    );
    focusable()[0]?.focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) {
        event.preventDefault();
        onCancel();
        return;
      }
      if (event.key !== "Tab") return;
      const items = focusable();
      if (!items.length) return;
      const current = document.activeElement;
      const index = items.indexOf(current as HTMLButtonElement);
      const nextIndex = event.shiftKey
        ? (index <= 0 ? items.length - 1 : index - 1)
        : (index < 0 || index === items.length - 1 ? 0 : index + 1);
      event.preventDefault();
      items[nextIndex].focus();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, [busy, onCancel, open]);

  if (!open || !project || !canonical || !model || !profile) return null;

  return (
    <div className="generation-review-backdrop" role="presentation" onMouseDown={() => !busy && onCancel()}>
      <section
        ref={dialogRef}
        className="generation-review-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="generation-review-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header>
          <span className="workspace-kicker"><i /> 确认生成</span>
          <h2 id="generation-review-title">确认这次 3D 生成</h2>
          <p>提交后会把标准化前景上传到 Modal，创建一个新的云端生成任务。原图不会离开本机。</p>
        </header>

        <div className="generation-review-grid">
          <div><small>项目</small><strong>{project.title}</strong><span>{project.source_name}</span></div>
          <div><small>模型</small><strong>{model.name}</strong><span>{model.output === "textured" ? "带纹理输出" : "仅几何输出"} · 约 {model.warm_seconds.toFixed(model.warm_seconds < 10 ? 1 : 0)}s</span></div>
          <div><small>参数</small><strong>{profile.name}</strong><span>参数模板 · {profile.id}</span></div>
          <div><small>前景</small><strong>{selectedComponents}/{componentCount || selectedComponents} 个物体</strong><span>当前选择将固定进本次生成</span></div>
          <div><small>标准图</small><strong>{fileSize(canonical.bytes)}</strong><span>SHA {canonical.sha256.slice(0, 12)}…</span></div>
          <div><small>上传内容</small><strong>仅标准化前景</strong><span>不上传原始图片，云端只接收处理后的前景</span></div>
        </div>

        <div className="generation-review-note">
          <i />
          <span><strong>这是一次新的云端任务。</strong><small>应用会做本地幂等保护；提交结果不确定时不会自动重复提交，避免重复计费。</small></span>
        </div>

        <footer>
          <button type="button" className="quiet-button" disabled={busy} onClick={onCancel}>返回检查</button>
          <button type="button" className="primary-button" disabled={busy} onClick={onConfirm}>
            {busy ? "正在提交…" : `确认 · 用 ${model.name} 生成`}
          </button>
        </footer>
      </section>
    </div>
  );
}
