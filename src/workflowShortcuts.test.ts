import { describe, expect, it } from "vitest";
import { workflowShortcutAction } from "./workflowShortcuts";

function keyEvent(
  key: string,
  modifiers: Partial<Pick<KeyboardEvent, "ctrlKey" | "metaKey" | "altKey" | "shiftKey">> = {},
) {
  return {
    key,
    ctrlKey: false,
    metaKey: false,
    altKey: false,
    shiftKey: false,
    ...modifiers,
  };
}

describe("workflowShortcutAction", () => {
  it("maps workflow navigation shortcuts", () => {
    expect(workflowShortcutAction(keyEvent("1", { altKey: true }))).toBe("prepare");
    expect(workflowShortcutAction(keyEvent("2", { altKey: true }))).toBe("generate");
  });

  it("maps Windows and macOS primary shortcuts", () => {
    expect(workflowShortcutAction(keyEvent("Enter", { ctrlKey: true }))).toBe("submit");
    expect(workflowShortcutAction(keyEvent(",", { ctrlKey: true }))).toBe("settings");
    expect(workflowShortcutAction(keyEvent(",", { metaKey: true }))).toBe("settings");
  });

  it("rejects ambiguous modifier combinations", () => {
    expect(workflowShortcutAction(keyEvent("1", { altKey: true, ctrlKey: true }))).toBeNull();
    expect(workflowShortcutAction(keyEvent("Enter", { ctrlKey: true, shiftKey: true }))).toBeNull();
    expect(workflowShortcutAction(keyEvent("z", { ctrlKey: true }))).toBeNull();
  });
});
