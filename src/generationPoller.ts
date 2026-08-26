import type { GenerationJob } from "./agent";
import { isJobActive } from "./generationState";

export const POLL_INTERVAL_MS = 1_400;
export const MAX_RETRY_DELAY_MS = 10_000;

export type GenerationPollerOptions = {
  getJob: () => Promise<GenerationJob>;
  getArtifact: () => Promise<Blob>;
  isCurrent: () => boolean;
  sleep?: (milliseconds: number) => Promise<void>;
  onJob: (job: GenerationJob) => void;
  onTransientError: (error: unknown, artifact: boolean) => void;
  onSucceeded: (job: GenerationJob, artifact: Blob) => Promise<void> | void;
  onTerminal: (job: GenerationJob) => Promise<void> | void;
};

const defaultSleep = (milliseconds: number) =>
  new Promise<void>((resolve) => setTimeout(resolve, milliseconds));

const nextRetryDelay = (current: number) =>
  Math.min(current * 2, MAX_RETRY_DELAY_MS);

export async function pollGenerationJob({
  getJob,
  getArtifact,
  isCurrent,
  sleep = defaultSleep,
  onJob,
  onTransientError,
  onSucceeded,
  onTerminal,
}: GenerationPollerOptions): Promise<void> {
  let retryDelay = POLL_INTERVAL_MS;

  while (isCurrent()) {
    let job: GenerationJob;
    try {
      job = await getJob();
      retryDelay = POLL_INTERVAL_MS;
    } catch (error) {
      if (!isCurrent()) return;
      onTransientError(error, false);
      await sleep(retryDelay);
      retryDelay = nextRetryDelay(retryDelay);
      continue;
    }

    if (!isCurrent()) return;
    onJob(job);
    if (!isCurrent()) return;
    if (isJobActive(job)) {
      await sleep(POLL_INTERVAL_MS);
      continue;
    }

    if (job.status === "succeeded" && job.result) {
      try {
        const artifact = await getArtifact();
        if (!isCurrent()) return;
        await onSucceeded(job, artifact);
        if (!isCurrent()) return;
      } catch (error) {
        if (!isCurrent()) return;
        onTransientError(error, true);
        await sleep(retryDelay);
        retryDelay = nextRetryDelay(retryDelay);
        continue;
      }
    }

    await onTerminal(job);
    return;
  }
}
