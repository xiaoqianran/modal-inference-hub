import { useCallback, useEffect, useRef, useState } from "react";

export type CommandFeedbackTone = "success" | "warning" | "error" | "info";

export type CommandFeedback = {
  id: number;
  tone: CommandFeedbackTone;
  title: string;
  detail?: string;
  action?: { label: string; run: () => void };
};

type FeedbackInput = Omit<CommandFeedback, "id">;

export function useCommandFeedback() {
  const [feedback, setFeedback] = useState<CommandFeedback | null>(null);
  const sequenceRef = useRef(0);
  const timerRef = useRef<number | null>(null);

  const dismiss = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    setFeedback(null);
  }, []);

  const notify = useCallback((input: FeedbackInput, duration = 3_800) => {
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    const id = ++sequenceRef.current;
    setFeedback({ ...input, id });
    timerRef.current = window.setTimeout(() => {
      setFeedback((current) => current?.id === id ? null : current);
      timerRef.current = null;
    }, duration);
  }, []);

  useEffect(() => () => {
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
  }, []);

  return { feedback, notify, dismiss };
}
