import { invoke } from "@tauri-apps/api/core";

export type AgentInfo = {
  running: boolean;
  port: number | null;
  session_token: string | null;
};

export type ModalCredentials = {
  token_id: string;
  token_secret: string;
};

export type CredentialStatus = {
  supported: boolean;
  stored: boolean;
};

export type AppDiagnostics = {
  version: string;
  data_dir: string;
  agent_log: string | null;
};

export type CanonicalAsset = {
  id: string;
  role: "canonical-rgba";
  mime: "image/png";
  bytes: number;
  sha256: string;
};

export type GenerationResult = {
  model: string;
  primary_artifact_id: string;
  artifact: {
    id: string;
    role: string;
    bytes: number;
    sha256: string;
    mime: string;
    producer?: {
      model: string;
      worker_app: string | null;
      revision: string | null;
    };
    created_at?: string;
    expires_at?: string | null;
  };
  timing: {
    load_s?: number;
    inference_s?: number;
  };
  metrics: Record<string, unknown>;
};

export type ModelProfile = {
  id: string;
  name: string;
};

export type ModelSpec = {
  id: string;
  name: string;
  description: string;
  status: "enabled" | "degraded" | "disabled";
  output: "geometry" | "textured";
  warm_seconds: number;
  profiles: ModelProfile[];
};



export type ModelDownloadState = {
  status: "idle" | "downloading" | "verifying" | "ready" | "failed";
  downloaded_bytes: number;
  total_bytes: number;
  progress: number;
  resumable: boolean;
  error: string | null;
  integrity: "unverified" | "verifying" | "verified" | "failed";
};

export type PreprocessRuntimeStatus = {
  engine: string;
  provider: "cpu" | "gpu";
  provider_preference: "cpu" | "gpu";
  available_providers: ("cpu" | "gpu")[];
  ort_providers: string[];
  gpu_available: boolean;
  gpu_warm: boolean;
  fallback_reason: string | null;
  model_home: string;
  model_path: string;
  model_downloaded: boolean;
  model_bytes: number;
  download: ModelDownloadState;
  canonical_size: number;
  cpu_threads: number;
  local_only: boolean;
};

export type RuntimeCapabilities = {
  hardware: {
    platform: string;
    machine: string;
    memory_mib: number | null;
    disk_free_mib: number;
    gpus: { name: string; memory_mib: number; driver: string }[];
  };
  preprocessing: PreprocessRuntimeStatus & { kind: "rembg" };
};

export type ForegroundComponent = {
  id: string;
  bbox: [number, number, number, number];
  area_pixels: number;
  foreground_ratio: number;
  image_ratio: number;
  selected: boolean;
};

export type ComponentState = {
  source_size: [number, number];
  components: ForegroundComponent[];
  selected_component_ids: string[];
  component_count: number;
  raw_component_count: number;
  ignored_component_count: number;
  ignored_foreground_pixels?: number;
  minimum_component_pixels?: number;
  foreground_bbox?: [number, number, number, number];
  selection_elapsed_ms?: number;
};

export type PreprocessResult = {
  project: Project;
  canonical: CanonicalAsset;
  matte: { mime: "image/png"; bytes: number; sha256: string };
  component_state: ComponentState;
  preprocess: {
    engine: string;
    provider: string;
    elapsed_ms: number;
    source_size: [number, number];
    foreground_bbox: [number, number, number, number];
    foreground_ratio: number;
    canonical_size: [number, number];
    component_count?: number;
    raw_component_count?: number;
    ignored_component_count?: number;
  };
};

export type ComponentSelectionResult = {
  project: Project;
  canonical: CanonicalAsset;
  component_state: ComponentState;
};

export type Project = {
  id: string;
  title: string;
  source_name: string;
  source_bytes: number;
  canonical_id: string | null;
  canonical_sha256: string | null;
  canonical_bytes: number | null;
  model: string | null;
  profile: string | null;
  job_id: string | null;
  artifact_id: string | null;
  artifact_sha256: string | null;
  artifact_bytes: number | null;
  artifact_canonical_sha256: string | null;
  status:
    | "draft"
    | "segmented"
    | "ready"
    | "generating"
    | "running"
    | "connection_required"
    | "cancel_requested"
    | "succeeded"
    | "failed"
    | "cancelled"
    | "expired";
  error: string | null;
  created_at: string;
  updated_at: string;
};


export type PreparedExport = {
  id: string;
  bytes: number;
  sha256: string;
};

export type GenerationJobStatus =
  | "running"
  | "connection_required"
  | "cancel_requested"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "expired";

export type GenerationJob = {
  id: string;
  model: string;
  status: GenerationJobStatus;
  created_at: string;
  updated_at: string;
  result: GenerationResult | null;
  error: string | null;
  error_code: string | null;
  retryable: boolean | null;
};

export const startAgent = () => invoke<AgentInfo>("agent_start");
export const agentStatus = () => invoke<AgentInfo>("agent_status");
export const stopAgent = () => invoke<void>("agent_stop");
export const credentialsStatus = () => invoke<CredentialStatus>("credentials_status");
export const saveCredentials = (credentials: ModalCredentials) =>
  invoke<void>("credentials_save", { credentials });
export const clearCredentials = () => invoke<void>("credentials_clear");
export const getAppDiagnostics = () => invoke<AppDiagnostics>("app_diagnostics");
export const revealAppData = () => invoke<void>("reveal_app_data");

const DEFAULT_REQUEST_TIMEOUT_MS = 120_000;

async function request(
  info: AgentInfo,
  path: string,
  init: RequestInit = {},
  timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS,
) {
  if (!info.running || !info.port || !info.session_token) throw new Error("本地代理尚未运行");
  const headers = new Headers(init.headers);
  headers.set("X-Modal-3D-Session", info.session_token);
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const controller = new AbortController();
  const upstream = init.signal;
  const abort = () => controller.abort();
  if (upstream?.aborted) controller.abort();
  else upstream?.addEventListener("abort", abort, { once: true });
  const timer = window.setTimeout(abort, timeoutMs);
  try {
    const response = await fetch(`http://127.0.0.1:${info.port}${path}`, {
      ...init,
      headers,
      signal: controller.signal,
    });
    if (!response.ok) {
      const body = (await response.json().catch(() => null)) as { detail?: string } | null;
      throw new Error(body?.detail || `本地代理请求失败（状态码 ${response.status}）`);
    }
    return response;
  } catch (error) {
    if (controller.signal.aborted && !upstream?.aborted) {
      throw new Error(`本地服务在 ${Math.round(timeoutMs / 1000)} 秒内没有响应`);
    }
    throw error;
  } finally {
    window.clearTimeout(timer);
    upstream?.removeEventListener("abort", abort);
  }
}

async function json<T>(info: AgentInfo, path: string, init?: RequestInit, timeoutMs?: number) {
  return (await request(info, path, init, timeoutMs)).json() as Promise<T>;
}

export const probeAgent = (info: AgentInfo) =>
  request(info, "/health", {}, 5_000).then((response) => response.json() as Promise<{ ok: boolean }>);
export const modalStatus = (info: AgentInfo) => json<{ connected: boolean }>(info, "/modal/status");

export const connectModal = (info: AgentInfo, credentials: ModalCredentials) =>
  json<{ ok: boolean }>(info, "/modal/connect", {
    method: "POST",
    body: JSON.stringify(credentials),
  });

export async function disconnectModal(info: AgentInfo) {
  await request(info, "/modal/connect", { method: "DELETE" });
}

export async function assetBlob(info: AgentInfo, path: string) {
  return (await request(info, `/v1/assets?path=${encodeURIComponent(path)}`, {}, 600_000)).blob();
}

export async function jobArtifactBlob(info: AgentInfo, jobId: string) {
  return (await request(info, `/v1/jobs/${jobId}/artifact`, {}, 600_000)).blob();
}

export const prepareExport = (info: AgentInfo, jobId: string) =>
  json<PreparedExport>(info, "/v1/exports", {
    method: "POST",
    body: JSON.stringify({ job_id: jobId }),
  });

export const savePreparedExport = (exportId: string, suggestedName: string) =>
  invoke<string | null>("export_save", { exportId, suggestedName });

export function createProject(info: AgentInfo, image: File) {
  const form = new FormData();
  form.append("file", image);
  return json<Project>(info, "/v1/projects", { method: "POST", body: form });
}

export const listProjects = (info: AgentInfo) =>
  json<Project[]>(info, "/v1/projects");

export const getProject = (info: AgentInfo, projectId: string) =>
  json<Project>(info, `/v1/projects/${projectId}`);

export async function deleteProject(info: AgentInfo, projectId: string) {
  return (await request(info, `/v1/projects/${projectId}`, { method: "DELETE" })).json() as Promise<{ deleted: string }>;
}

export async function projectSourceBlob(info: AgentInfo, projectId: string) {
  return (await request(info, `/v1/projects/${projectId}/source`)).blob();
}

export async function projectCanonicalBlob(info: AgentInfo, projectId: string) {
  return (await request(info, `/v1/projects/${projectId}/canonical`, {}, 600_000)).blob();
}

export async function projectMatteBlob(info: AgentInfo, projectId: string) {
  return (await request(info, `/v1/projects/${projectId}/matte`, {}, 600_000)).blob();
}

export async function projectSelectionBlob(info: AgentInfo, projectId: string) {
  return (await request(info, `/v1/projects/${projectId}/selection`, {}, 600_000)).blob();
}

export const preprocessProject = (info: AgentInfo, projectId: string) =>
  json<PreprocessResult>(info, `/v1/projects/${projectId}/preprocess`, {
    method: "POST",
  }, 600_000);

export const getProjectComponents = (info: AgentInfo, projectId: string) =>
  json<{ component_state: ComponentState; canonical: CanonicalAsset }>(
    info,
    `/v1/projects/${projectId}/components`,
  );

export const selectProjectComponents = (
  info: AgentInfo,
  projectId: string,
  selectedComponentIds: string[],
) =>
  json<ComponentSelectionResult>(info, `/v1/projects/${projectId}/components`, {
    method: "POST",
    body: JSON.stringify({ selected_component_ids: selectedComponentIds }),
  });

export const submitProjectGeneration = (
  info: AgentInfo,
  projectId: string,
  model: string,
  profile: string,
) =>
  json<{ project: Project; job: GenerationJob }>(info, `/v1/projects/${projectId}/generation`, {
    method: "POST",
    body: JSON.stringify({ model, profile, seed: 42 }),
  });

export const getCapabilities = (info: AgentInfo) =>
  json<RuntimeCapabilities>(info, "/v1/capabilities");

export const getPreprocessStatus = (info: AgentInfo) =>
  json<PreprocessRuntimeStatus>(info, "/v1/preprocess/status");

export const preparePreprocessModel = (info: AgentInfo) =>
  json<PreprocessRuntimeStatus>(info, "/v1/preprocess/model", { method: "POST" });

export const setPreprocessProvider = (
  info: AgentInfo,
  provider: "cpu" | "gpu",
) =>
  json<PreprocessRuntimeStatus>(info, "/v1/preprocess/provider", {
    method: "POST",
    body: JSON.stringify({ provider }),
  });

export const listModels = (info: AgentInfo) =>
  json<ModelSpec[]>(info, "/v1/models");

export const getJob = (info: AgentInfo, jobId: string) =>
  json<GenerationJob>(info, `/v1/jobs/${jobId}`);

export const cancelJob = (info: AgentInfo, jobId: string) =>
  json<GenerationJob>(info, `/v1/jobs/${jobId}`, { method: "DELETE" });
