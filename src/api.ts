import { invoke } from "@tauri-apps/api/core";

export type ProviderModel = {
  id: string;
  name?: string;
  status?: string;
  profiles?: Array<{ id: string; name?: string }>;
};

export type Provider = {
  id: "modal-2d" | "modal-3d";
  reachable: boolean;
  connected: boolean;
  models: ProviderModel[];
  error?: string;
};

export type ModalCredentials = {
  tokenId: string;
  tokenSecret: string;
};

export type DeploymentPlan = {
  provider: Provider["id"];
  apps: string[];
  steps: Array<{ id: string; label: string }>;
};

export type Deployment = {
  id: string;
  provider: Provider["id"];
  state: "queued" | "running" | "succeeded" | "failed";
  stage: string;
  events: Array<{ stage: string; state: string; message: string; at: string }>;
  result?: Record<string, unknown> | null;
  error?: string | null;
  createdAt: string;
  updatedAt: string;
};

export type InputDescriptor = {
  sha256: string;
  bytes: number;
  mediaType: "image/png" | "image/jpeg" | "image/webp";
  name: string;
};

export type BatchItem = {
  id: string;
  ordinal: number;
  source: Record<string, unknown>;
  state: string;
  target: { kind: "experiment" | "direct-image"; id: string };
  error?: string | null;
};

export type Batch = {
  id: string;
  kind: "prompts" | "images";
  state: string;
  summary: Record<string, number> & { total: number };
  items: BatchItem[];
  createdAt: string;
  updatedAt: string;
};

const credentialTokens = (value: string): string[] => {
  if (!value.trim()) throw new Error("请输入 Modal Token");
  if (/[\r\n\0;&|<>`]/.test(value) || value.includes("$(")) {
    throw new Error("凭据输入包含不支持的命令字符");
  }
  const tokens: string[] = [];
  let token = "";
  let quote = "";
  for (const char of value) {
    if (quote) {
      if (char === quote) quote = "";
      else token += char;
    } else if (char === '"' || char === "'") {
      quote = char;
    } else if (/\s/.test(char)) {
      if (token) {
        tokens.push(token);
        token = "";
      }
    } else {
      token += char;
    }
  }
  if (quote) throw new Error("凭据输入中的引号没有闭合");
  if (token) tokens.push(token);
  return tokens;
};

/** 只导入本产品需要的两种最小格式；不会执行命令，也不复制 Modal CLI Schema。 */
export const parseModalCredentials = (value: string): ModalCredentials => {
  const tokens = credentialTokens(value.trim());
  const prefix = tokens.slice(0, 3).map((item) => item.toLowerCase()).join(" ");
  const args = prefix === "modal token set" ? tokens.slice(3) : tokens;
  const fields: Partial<ModalCredentials> = {};
  const positional: string[] = [];

  for (let index = 0; index < args.length; index += 1) {
    const item = args[index];
    const separator = item.indexOf("=");
    const flag = separator < 0 ? item : item.slice(0, separator);
    const inline = separator < 0 ? "" : item.slice(separator + 1);
    if (flag === "--token-id" || flag === "--token-secret") {
      const next = inline || args[index + 1];
      if (!next || next.startsWith("--")) throw new Error("Modal Token 参数缺少值");
      if (!inline) index += 1;
      const key = flag === "--token-id" ? "tokenId" : "tokenSecret";
      if (fields[key]) throw new Error("Modal Token 参数重复");
      fields[key] = next;
    } else if (item.startsWith("--")) {
      throw new Error("只支持 token-id 和 token-secret 参数");
    } else {
      positional.push(item);
    }
  }

  if (!fields.tokenId && !fields.tokenSecret && positional.length === 2) {
    fields.tokenId = positional.find((item) => item.startsWith("ak-"));
    fields.tokenSecret = positional.find((item) => item.startsWith("as-"));
  } else if (positional.length) {
    throw new Error("无法识别 Modal Token 输入");
  }
  if (!fields.tokenId?.startsWith("ak-") || !fields.tokenSecret?.startsWith("as-")) {
    throw new Error("无法识别 Token ID 或 Token Secret");
  }
  return { tokenId: fields.tokenId, tokenSecret: fields.tokenSecret };
};

export type Candidate = {
  id: string;
  ordinal: number;
  seed: number;
  job: {
    provider: "modal-2d";
    id: string;
    state: string;
    failure?: string | null;
    retryable?: boolean | null;
  };
  artifact: Record<string, unknown> | null;
  failure: string | null;
};

export type Experiment = {
  id: string;
  title: string;
  prompt: string;
  phase: string;
  createdAt: string;
  updatedAt: string;
  image: { model: string; candidates: Candidate[] };
  selection: { candidateId: string; selectedAt: string } | null;
  asset3d: null | {
    model: string;
    profile: string;
    seed: number;
    job: { id: string; state: string; failure?: string | null };
    artifact: Record<string, unknown> | null;
    conditioning: Record<string, unknown> | null;
  };
};

export const isExperimentActive = (phase: string) =>
  phase === "generating-images" ||
  (phase.startsWith("asset3d-") &&
    !["asset3d-succeeded", "asset3d-failed", "asset3d-cancelled", "asset3d-expired"].includes(phase));

type AgentInfo = {
  running: boolean;
  port: number | null;
  session_token: string | null;
};

export class HubApi {
  constructor(
    private readonly baseUrl: string,
    private readonly token: string | null,
  ) {}

  static async connect(): Promise<HubApi> {
    const tauri = "__TAURI_INTERNALS__" in window;
    if (!tauri) {
      return new HubApi(import.meta.env.VITE_HUB_URL ?? "http://127.0.0.1:39001", null);
    }
    const info = await invoke<AgentInfo>("agent_start");
    if (!info.running || !info.port || !info.session_token) {
      throw new Error("本地 Hub 未能启动");
    }
    return new HubApi(`http://127.0.0.1:${info.port}`, info.session_token);
  }

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      ...init,
      headers: {
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...(this.token ? { "X-Modal-Hub-Session": this.token } : {}),
        ...init?.headers,
      },
    });
    if (!response.ok) {
      const value = await response.json().catch(() => ({ detail: response.statusText }));
      throw new Error(String(value.detail ?? response.statusText));
    }
    return response.json() as Promise<T>;
  }

  providers = () => this.request<{ providers: Provider[] }>("/api/providers");
  connectProvider = (provider: string, tokenId: string, tokenSecret: string) =>
    this.request<{ connected: boolean }>(`/api/providers/${provider}/connection`, {
      method: "POST",
      body: JSON.stringify({ token_id: tokenId, token_secret: tokenSecret }),
    });
  deploymentPlan = (provider: string) =>
    this.request<DeploymentPlan>(`/api/providers/${provider}/deployment-plan`);
  startDeployment = (provider: string, tokenId: string, tokenSecret: string) =>
    this.request<Deployment>(`/api/providers/${provider}/deployments`, {
      method: "POST",
      body: JSON.stringify({ token_id: tokenId, token_secret: tokenSecret }),
    });
  deployment = (id: string) => this.request<Deployment>(`/api/deployments/${id}`);
  ingestImage = (file: File) =>
    this.request<InputDescriptor>("/api/inputs/images", {
      method: "POST",
      body: file,
      headers: {
        "Content-Type": "application/octet-stream",
        "X-File-Name": file.name.replace(/[^\x20-\x7e]/g, "_"),
      },
    });
  createPromptBatch = (body: object) =>
    this.request<Batch>("/api/batches/prompts", {
      method: "POST",
      body: JSON.stringify(body),
    });
  createImageBatch = (body: object) =>
    this.request<Batch>("/api/batches/images", {
      method: "POST",
      body: JSON.stringify(body),
    });
  batches = () => this.request<{ batches: Batch[] }>("/api/batches");
  batch = (id: string) => this.request<Batch>(`/api/batches/${id}`);
  resumeBatch = (id: string) =>
    this.request<Batch>(`/api/batches/${id}/resume`, { method: "POST" });
  experiments = () => this.request<{ experiments: Experiment[] }>("/api/experiments");
  experiment = (id: string) => this.request<Experiment>(`/api/experiments/${id}`);
  create = (body: object) =>
    this.request<Experiment>("/api/experiments", { method: "POST", body: JSON.stringify(body) });
  select = (id: string, candidateId: string) =>
    this.request<Experiment>(`/api/experiments/${id}/selection`, {
      method: "POST",
      body: JSON.stringify({ candidate_id: candidateId }),
    });
  generate3d = (id: string, body: object) =>
    this.request<Experiment>(`/api/experiments/${id}/asset3d`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  resume = (id: string) =>
    this.request<Experiment>(`/api/experiments/${id}/resume`, { method: "POST" });

  async artifactUrl(path: string): Promise<string> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      headers: this.token ? { "X-Modal-Hub-Session": this.token } : {},
    });
    if (!response.ok) throw new Error("产物暂不可用");
    return URL.createObjectURL(await response.blob());
  }
}
