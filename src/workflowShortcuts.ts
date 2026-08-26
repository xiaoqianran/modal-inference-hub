export type WorkflowShortcutAction = "prepare" | "generate" | "submit" | "settings" | null;

type ShortcutEvent = Pick<
  KeyboardEvent,
  "key" | "ctrlKey" | "metaKey" | "altKey" | "shiftKey"
>;

export function workflowShortcutAction(event: ShortcutEvent): WorkflowShortcutAction {
  const primary = event.ctrlKey || event.metaKey;
  const key = event.key.toLowerCase();

  if (primary && !event.altKey && !event.shiftKey && key === ",") return "settings";
  if (event.altKey && !primary && !event.shiftKey && key === "1") return "prepare";
  if (event.altKey && !primary && !event.shiftKey && key === "2") return "generate";
  if (primary && !event.altKey && !event.shiftKey && event.key === "Enter") return "submit";
  return null;
}
