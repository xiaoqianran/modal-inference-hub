import { invoke } from "@tauri-apps/api/core";

export type AgentInfo = {
  running: boolean;
  port: number | null;
  session_token: string | null;
};

export const startAgent = () => invoke<AgentInfo>("agent_start");
export const agentStatus = () => invoke<AgentInfo>("agent_status");
export const stopAgent = () => invoke<void>("agent_stop");

export async function probeAgent(info: AgentInfo) {
  if (!info.running || !info.port || !info.session_token) throw new Error("Agent is not running");
  const response = await fetch(`http://127.0.0.1:${info.port}/health`, {
    headers: { "X-Modal-3D-Session": info.session_token },
  });
  if (!response.ok) throw new Error(`Agent health check failed (${response.status})`);
  return response.json() as Promise<{ ok: boolean }>;
}
