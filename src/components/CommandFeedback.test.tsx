import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import CommandFeedback from "./CommandFeedback";

describe("CommandFeedback", () => {
  it("renders recovery actions for errors", () => {
    const html = renderToStaticMarkup(
      <CommandFeedback
        feedback={{
          id: 1,
          tone: "error",
          title: "本地预处理失败",
          detail: "model unavailable",
          action: { label: "重试 rembg", run: () => undefined },
        }}
        onDismiss={() => undefined}
      />,
    );

    expect(html).toContain('role="alert"');
    expect(html).toContain("重试 rembg");
    expect(html).toContain("model unavailable");
  });

  it("renders nothing without feedback", () => {
    expect(renderToStaticMarkup(
      <CommandFeedback feedback={null} onDismiss={() => undefined} />,
    )).toBe("");
  });
});
