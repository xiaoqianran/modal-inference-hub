import { describe, expect, it } from "vitest";
import type { GenerationJob, Project } from "./agent";
import { isJobActive, isProjectGenerationActive } from "./generationState";

const job = (status: GenerationJob["status"]): GenerationJob => ({
  id: "job-1",
  model: "pixal3d",
  status,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  result: null,
  error: null,
  error_code: null,
  retryable: null,
});

describe("generation state", () => {
  it.each(["running", "connection_required", "cancel_requested"] as const)(
    "treats %s as an active job",
    (status) => expect(isJobActive(job(status))).toBe(true),
  );

  it.each(["succeeded", "failed", "cancelled", "expired"] as const)(
    "treats %s as a terminal job",
    (status) => expect(isJobActive(job(status))).toBe(false),
  );

  it.each([
    "submitting",
    "submission_unknown",
    "generating",
    "running",
    "connection_required",
    "cancel_requested",
  ] as Project["status"][])("locks project generation while %s", (status) => {
    expect(isProjectGenerationActive(status)).toBe(true);
  });

  it.each(["draft", "segmented", "ready", "succeeded", "failed", "cancelled", "expired"] as Project["status"][])(
    "does not lock project generation while %s",
    (status) => expect(isProjectGenerationActive(status)).toBe(false),
  );
});
