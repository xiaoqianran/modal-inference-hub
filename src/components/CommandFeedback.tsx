import type { CommandFeedback as Feedback } from "../hooks/useCommandFeedback";

type CommandFeedbackProps = {
  feedback: Feedback | null;
  onDismiss: () => void;
};

const toneLabels: Record<Feedback["tone"], string> = {
  success: "完成",
  warning: "注意",
  error: "出错",
  info: "提示",
};

export default function CommandFeedback({ feedback, onDismiss }: CommandFeedbackProps) {
  if (!feedback) return null;
  return (
    <div
      className={`command-feedback ${feedback.tone} ${feedback.action ? "has-action" : ""}`}
      role={feedback.tone === "error" ? "alert" : "status"}
    >
      <i />
      <span>
        <small>{toneLabels[feedback.tone]}</small>
        <strong>{feedback.title}</strong>
        {feedback.detail ? <em>{feedback.detail}</em> : null}
      </span>
      {feedback.action ? (
        <button
          type="button"
          className="command-feedback-action"
          onClick={() => {
            onDismiss();
            feedback.action?.run();
          }}
        >
          {feedback.action.label}
        </button>
      ) : null}
      <button type="button" className="command-feedback-close" aria-label="关闭提示" onClick={onDismiss}>×</button>
    </div>
  );
}
