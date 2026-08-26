type WorkflowTarget = "prepare" | "generate";

type WorkflowProgressProps = {
  stage: number;
  message: string;
  generationActive: boolean;
  generationCount: number;
  modelName?: string;
  onNavigate: (target: WorkflowTarget) => void;
};

const steps = [
  { id: "source", index: "01", label: "原图", eyebrow: "SOURCE", target: "prepare" as const },
  { id: "cutout", index: "02", label: "前景", eyebrow: "CUTOUT", target: "prepare" as const },
  { id: "generate", index: "03", label: "生成", eyebrow: "REBUILD", target: "generate" as const },
  { id: "result", index: "04", label: "结果", eyebrow: "OUTPUT", target: "generate" as const },
] as const;

function stepMeta(index: number, stage: number, generationActive: boolean, generationCount: number, modelName?: string) {
  if (index === 0) return stage >= 1 ? "本地已保存" : "等待导入";
  if (index === 1) return stage >= 2 ? "Canonical ready" : stage >= 1 ? "等待 rembg" : "等待原图";
  if (index === 2) return generationActive ? "云端运行中" : modelName || "选择模型";
  return stage >= 3 ? `${generationCount || 1} 个模型版本` : generationCount ? `${generationCount} 个历史版本` : "等待 GLB";
}

export default function WorkflowProgress({
  stage,
  message,
  generationActive,
  generationCount,
  modelName,
  onNavigate,
}: WorkflowProgressProps) {
  return (
    <section className="workflow-console" aria-label="项目工作流">
      <div className="workflow-console-head">
        <div>
          <span className="workspace-kicker"><i /> PROJECT PIPELINE</span>
          <strong>从原图到可导出的 3D 资产</strong>
        </div>
        <p className="workflow-message" role="status" aria-live="polite">{message}</p>
      </div>

      <ol className="workflow-progress">
        {steps.map((item, index) => {
          const complete = stage > index || (stage === 3 && index === 3);
          const current = stage === index;
          const active = item.id === "generate" && generationActive;
          return (
            <li key={item.id} className={`${complete ? "complete" : ""} ${current ? "current" : ""} ${active ? "running" : ""}`}>
              <button type="button" onClick={() => onNavigate(item.target)} aria-current={current ? "step" : undefined}>
                <span className="workflow-index">{complete ? "✓" : item.index}</span>
                <span className="workflow-step-copy">
                  <small>{item.eyebrow}</small>
                  <strong>{item.label}</strong>
                </span>
                <span className="workflow-step-meta">
                  {stepMeta(index, stage, generationActive, generationCount, modelName)}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
