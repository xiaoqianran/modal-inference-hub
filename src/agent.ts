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

export type SamCandidate = {
  candidate_id: string;
  rank: number;
  score: number;
  model_bbox_xyxy_norm: [number, number, number, number];
};


export type RefinementBox = {
  cx: number;
  cy: number;
  width: number;
  height: number;
  positive: boolean;
};

export type SamSelection = {
  scene_id: string;
  selection_id: string;
  image_size: [number, number];
  candidate_count: number;
  candidates: SamCandidate[];
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



export type SamMode = "auto" | "cloud" | "local";

export type RuntimeCapabilities = {
  hardware: {
    platform: string;
    machine: string;
    memory_mib: number | null;
    disk_free_mib: number;
    gpus: { name: string; memory_mib: number; driver: string }[];
  };
  sam: {
    mode: SamMode;
    effective: "cloud" | "local" | null;
    local: {
      available: boolean;
      ready: boolean;
      installed: boolean;
      runtime_installed: boolean;
      checkpoint_installed: boolean;
      installing: boolean;
      state: string;
      installed_version: string | null;
      expected_version: string;
      update_available: boolean;
      step: string | null;
      error: string | null;
      downloaded_bytes: number | null;
      download_total_bytes: number | null;
      download_speed_bps: number | null;
      download_eta_seconds: number | null;
      root_path: string;
      hardware_eligible: boolean;
      disk_eligible: boolean;
      min_disk_mib: number;
      supported_platform: boolean;
      reason: string;
      min_vram_mib: number;
      checkpoint_bytes: number;
      gpu: { name: string; memory_mib: number; driver: string } | null;
      health: {
        ready?: boolean;
        gpu?: string;
        vram_gib?: number;
        bf16?: boolean;
        model_load_s?: number;
      } | null;
    };
    cloud: { available: boolean };
  };
};

export type Project = {
  id: string;
  title: string;
  source_name: string;
  source_bytes: number;
  concept: string | null;
  sam_provider: "cloud" | "local" | null;
  scene_id: string | null;
  selection_id: string | null;
  candidate_id: string | null;
  canonical_id: string | null;
  canonical_sha256: string | null;
  canonical_bytes: number | null;
  model: string | null;
  profile: string | null;
  job_id: string | null;
  artifact_id: string | null;
  artifact_sha256: string | null;
  artifact_bytes: number | null;
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

async function json<T>(info: AgentInfo, path: string, init?: RequestInit) {
  return (await request(info, path, init)).json() as Promise<T>;
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

export const segmentProject = (info: AgentInfo, projectId: string, concept: string) =>
  json<{ project: Project; selection: SamSelection; provider: "cloud" | "local" }>(info, `/v1/projects/${projectId}/segment`, {
    method: "POST",
    body: JSON.stringify({ concept, max_candidates: 8 }),
  });

export const refineProject = (
  info: AgentInfo,
  projectId: string,
  boxes: RefinementBox[],
) =>
  json<{ project: Project; selection: SamSelection; provider: "cloud" | "local" }>(info, `/v1/projects/${projectId}/refine`, {
    method: "POST",
    body: JSON.stringify({ boxes, max_candidates: 8 }),
  });

export const materializeProject = (
  info: AgentInfo,
  projectId: string,
  candidateId: string,
) =>
  json<{ project: Project; canonical: CanonicalAsset }>(info, `/v1/projects/${projectId}/materialize`, {
    method: "POST",
    body: JSON.stringify({ candidate_id: candidateId, output_size: 1024 }),
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

export const installLocalSam = (info: AgentInfo) =>
  json<Record<string, unknown>>(info, "/v1/local-sam/install", { method: "POST" });

export const uninstallLocalSam = (info: AgentInfo) =>
  json<{ released_bytes: number }>(info, "/v1/local-sam/install", { method: "DELETE" });

export const migrateLocalSam = (info: AgentInfo, path: string) =>
  json<RuntimeCapabilities>(info, "/v1/local-sam/location", {
    method: "PUT",
    body: JSON.stringify({ path }),
  });

export const startLocalSam = (info: AgentInfo) =>
  json<Record<string, unknown>>(info, "/v1/local-sam/start", { method: "POST" });

export async function stopLocalSam(info: AgentInfo) {
  await request(info, "/v1/local-sam/start", { method: "DELETE" });
}

export const setSamMode = (info: AgentInfo, mode: SamMode) =>
  json<{ sam_mode: SamMode }>(info, "/v1/settings/sam", {
    method: "PUT",
    body: JSON.stringify({ mode }),
  });

export const listModels = (info: AgentInfo) =>
  json<ModelSpec[]>(info, "/v1/models");

export const getJob = (info: AgentInfo, jobId: string) =>
  json<GenerationJob>(info, `/v1/jobs/${jobId}`);

export const cancelJob = (info: AgentInfo, jobId: string) =>
  json<GenerationJob>(info, `/v1/jobs/${jobId}`, { method: "DELETE" });
