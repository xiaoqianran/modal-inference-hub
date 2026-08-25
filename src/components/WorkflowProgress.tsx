const steps = ["导入", "抠图", "生成"] as const;

export default function WorkflowProgress({ stage, message }: { stage: number; message: string }) {
  return (
    <div className="workflow-bar">
      <ol className="workflow-progress" aria-label="项目进度">
        {steps.map((label, index) => {
          const step = index + 1;
          const complete = stage >= step;
          return (
            <li key={label} className={`${complete ? "complete" : ""} ${stage === step ? "current" : ""}`}>
              <span>{complete ? "✓" : step}</span>
              <strong>{label}</strong>
            </li>
          );
        })}
      </ol>
      <p className="workflow-message" role="status" aria-live="polite">{message}</p>
    </div>
  );
}
