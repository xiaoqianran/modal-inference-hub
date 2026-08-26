import type { GenerationJob, GenerationJobStatus, Project } from "./agent";

const ACTIVE_JOB_STATUSES = new Set<GenerationJobStatus>([
  "running",
  "connection_required",
  "cancel_requested",
]);

const ACTIVE_PROJECT_STATUSES = new Set<Project["status"]>([
  "submitting",
  "submission_unknown",
  "generating",
  "running",
  "connection_required",
  "cancel_requested",
]);

export const isJobActive = (job: GenerationJob | null): boolean =>
  job !== null && ACTIVE_JOB_STATUSES.has(job.status);

export const isProjectGenerationActive = (status: Project["status"]): boolean =>
  ACTIVE_PROJECT_STATUSES.has(status);
