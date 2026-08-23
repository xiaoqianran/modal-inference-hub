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

export type SamCandidate = {
  candidate_id: string;
  rank: number;
  score: number;
  model_bbox_xyxy_norm: [number, number, number, number];
};

export type SamSelection = {
  scene_id: string;
  selection_id: string;
  image_size: [number, number];
  candidate_count: number;
  candidates: SamCandidate[];
};

export type CanonicalAsset = {
  scene_id: string;
  selection_id: string;
  candidate_id: string;
  canonical_path: string;
  canonical_bytes: number;
};

export type GenerationResult = {
  model: string;
  artifact: {
    path: string;
    bytes: number;
    mime: string;
  };
  timing: {
    load_s?: number;
    inference_s?: number;
  };
  metrics: Record<string, unknown>;
};

export type GenerationJob = {
  id: string;
  model: string;
  status: "running" | "succeeded" | "failed" | "cancelled" | "expired";
  created_at: string;
  result: GenerationResult | null;
  error: string | null;
};

export const startAgent = () => invoke<AgentInfo>("agent_start");
export const agentStatus = () => invoke<AgentInfo>("agent_status");
export const stopAgent = () => invoke<void>("agent_stop");
export const credentialsStatus = () => invoke<CredentialStatus>("credentials_status");
export const saveCredentials = (credentials: ModalCredentials) =>
  invoke<void>("credentials_save", { credentials });
export const clearCredentials = () => invoke<void>("credentials_clear");

async function request(info: AgentInfo, path: string, init: RequestInit = {}) {
  if (!info.running || !info.port || !info.session_token) throw new Error("本地代理尚未运行");
  const headers = new Headers(init.headers);
  headers.set("X-Modal-3D-Session", info.session_token);
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`http://127.0.0.1:${info.port}${path}`, { ...init, headers });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(body?.detail || `本地代理请求失败（状态码 ${response.status}）`);
  }
  return response;
}

async function json<T>(info: AgentInfo, path: string, init?: RequestInit) {
  return (await request(info, path, init)).json() as Promise<T>;
}

export const probeAgent = (info: AgentInfo) => json<{ ok: boolean }>(info, "/health");
export const modalStatus = (info: AgentInfo) => json<{ connected: boolean }>(info, "/modal/status");

export const connectModal = (info: AgentInfo, credentials: ModalCredentials) =>
  json<{ ok: boolean }>(info, "/modal/connect", {
    method: "POST",
    body: JSON.stringify(credentials),
  });

export async function disconnectModal(info: AgentInfo) {
  await request(info, "/modal/connect", { method: "DELETE" });
}

export function segmentImage(info: AgentInfo, image: File, concept: string) {
  const form = new FormData();
  form.append("image", image);
  form.append("concept", concept);
  return json<SamSelection>(info, "/v1/sam/segment", { method: "POST", body: form });
}

export const materializeCandidate = (
  info: AgentInfo,
  selection: SamSelection,
  candidateId: string,
) =>
  json<CanonicalAsset>(info, "/v1/sam/materialize", {
    method: "POST",
    body: JSON.stringify({
      scene_id: selection.scene_id,
      selection_id: selection.selection_id,
      candidate_id: candidateId,
      output_size: 1024,
    }),
  });

export async function assetBlob(info: AgentInfo, path: string) {
  return (await request(info, `/v1/assets?path=${encodeURIComponent(path)}`)).blob();
}

export const submitGeneration = (
  info: AgentInfo,
  inputPath: string,
  model = "fastsam3d-plus-plus",
) =>
  json<GenerationJob>(info, "/v1/generations", {
    method: "POST",
    body: JSON.stringify({ model, input_path: inputPath, options: { seed: 42 } }),
  });

export const getJob = (info: AgentInfo, jobId: string) =>
  json<GenerationJob>(info, `/v1/jobs/${jobId}`);

export const cancelJob = (info: AgentInfo, jobId: string) =>
  json<GenerationJob>(info, `/v1/jobs/${jobId}`, { method: "DELETE" });
