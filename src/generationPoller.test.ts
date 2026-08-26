import { describe, expect, it, vi } from "vitest";
import type { GenerationJob } from "./agent";
import { POLL_INTERVAL_MS, pollGenerationJob } from "./generationPoller";

const job = (
  status: GenerationJob["status"],
  options: { result?: boolean; error?: string | null } = {},
): GenerationJob => ({
  id: "job-1",
  model: "pixal3d",
  status,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  result: options.result
    ? {
        model: "pixal3d",
        primary_artifact_id: "artifact-1",
        artifact: {
          id: "artifact-1",
          role: "primary",
          bytes: 3,
          sha256: "abc",
          mime: "model/gltf-binary",
        },
        timing: {},
        metrics: {},
      }
    : null,
  error: options.error ?? null,
  error_code: null,
  retryable: null,
});

describe("pollGenerationJob", () => {
  it("recovers from a transient status read error", async () => {
    const getJob = vi
      .fn<() => Promise<GenerationJob>>()
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce(job("failed", { error: "worker failed" }));
    const sleep = vi.fn(async () => undefined);
    const transient = vi.fn();
    const terminal = vi.fn(async () => undefined);

    await pollGenerationJob({
      getJob,
      getArtifact: vi.fn(),
      isCurrent: () => true,
      sleep,
      onJob: vi.fn(),
      onTransientError: transient,
      onSucceeded: vi.fn(),
      onTerminal: terminal,
    });

    expect(getJob).toHaveBeenCalledTimes(2);
    expect(transient).toHaveBeenCalledWith(expect.any(Error), false);
    expect(sleep).toHaveBeenCalledWith(POLL_INTERVAL_MS);
    expect(terminal).toHaveBeenCalledOnce();
  });

  it("retries artifact retrieval without resubmitting the generation", async () => {
    const succeeded = job("succeeded", { result: true });
    const getArtifact = vi
      .fn<() => Promise<Blob>>()
      .mockRejectedValueOnce(new Error("cache unavailable"))
      .mockResolvedValueOnce(new Blob(["glb"]));
    const onSucceeded = vi.fn();
    const transient = vi.fn();

    await pollGenerationJob({
      getJob: vi.fn(async () => succeeded),
      getArtifact,
      isCurrent: () => true,
      sleep: vi.fn(async () => undefined),
      onJob: vi.fn(),
      onTransientError: transient,
      onSucceeded,
      onTerminal: vi.fn(),
    });

    expect(getArtifact).toHaveBeenCalledTimes(2);
    expect(transient).toHaveBeenCalledWith(expect.any(Error), true);
    expect(onSucceeded).toHaveBeenCalledOnce();
  });

  it("stops immediately when the caller invalidates the poll", async () => {
    let current = true;
    const onJob = vi.fn(() => {
      current = false;
    });
    const sleep = vi.fn(async () => undefined);

    await pollGenerationJob({
      getJob: vi.fn(async () => job("running")),
      getArtifact: vi.fn(),
      isCurrent: () => current,
      sleep,
      onJob,
      onTransientError: vi.fn(),
      onSucceeded: vi.fn(),
      onTerminal: vi.fn(),
    });

    expect(onJob).toHaveBeenCalledOnce();
    expect(sleep).not.toHaveBeenCalled();
  });
});
