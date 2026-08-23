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

export const startAgent = () => invoke<AgentInfo>("agent_start");
export const agentStatus = () => invoke<AgentInfo>("agent_status");
export const stopAgent = () => invoke<void>("agent_stop");

export type CredentialStatus = {
  supported: boolean;
  stored: boolean;
};

export const credentialsStatus = () => invoke<CredentialStatus>("credentials_status");
export const saveCredentials = (credentials: ModalCredentials) =>
  invoke<void>("credentials_save", { credentials });
export const clearCredentials = () => invoke<void>("credentials_clear");

async function request(info: AgentInfo, path: string, init?: RequestInit) {
  if (!info.running || !info.port || !info.session_token) throw new Error("Agent is not running");
  const response = await fetch(`http://127.0.0.1:${info.port}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-Modal-3D-Session": info.session_token,
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(body?.detail || `Local Agent request failed (${response.status})`);
  }
  return response;
}

export async function probeAgent(info: AgentInfo) {
  const response = await request(info, "/health");
  return response.json() as Promise<{ ok: boolean }>;
}

export async function connectModal(info: AgentInfo, credentials: ModalCredentials) {
  const response = await request(info, "/modal/connect", {
    method: "POST",
    body: JSON.stringify(credentials),
  });
  return response.json() as Promise<{ ok: boolean }>;
}

export async function modalStatus(info: AgentInfo) {
  const response = await request(info, "/modal/status");
  return response.json() as Promise<{ connected: boolean }>;
}

export async function disconnectModal(info: AgentInfo) {
  await request(info, "/modal/connect", { method: "DELETE" });
}
