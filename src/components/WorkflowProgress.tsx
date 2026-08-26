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
  { id: "source", index: "1", label: "导入原图", eyebrow: "本地", target: "prepare" as const },
  { id: "cutout", index: "2", label: "抠出前景", eyebrow: "本地", target: "prepare" as const },
  { id: "generate", index: "3", label: "云端生成", eyebrow: "Modal", target: "generate" as const },
  { id: "result", index: "4", label: "查看导出", eyebrow: "GLB", target: "generate" as const },
] as const;

function stepMeta(index: number, stage: number, generationActive: boolean, generationCount: number, modelName?: string) {
  if (index === 0) return stage >= 1 ? "已保存到本地" : "等待导入图片";
  if (index === 1) return stage >= 2 ? "前景已就绪" : stage >= 1 ? "等待抠图" : "等待原图";
  if (index === 2) return generationActive ? "云端生成中" : modelName || "选择模型";
  return stage >= 3 ? `${generationCount || 1} 个模型版本` : generationCount ? `${generationCount} 个历史版本` : "等待生成";
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
          <strong>从一张图片到可导出的 3D 模型</strong>
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
