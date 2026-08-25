import { useCallback, useEffect, useRef, useState } from "react";
import {
  agentStatus,
  clearCredentials,
  connectModal,
  credentialsStatus,
  disconnectModal,
  getAppDiagnostics,
  getCapabilities,
  listModels,
  modalStatus,
  probeAgent,
  saveCredentials,
  revealAppData,
  setPreprocessProvider,
  startAgent,
  stopAgent,
  type AgentInfo,
  type AppDiagnostics,
  type CredentialStatus,
  type ModelSpec,
  type RuntimeCapabilities,
} from "./agent";

const sleep = (milliseconds: number) => new Promise((resolve) => setTimeout(resolve, milliseconds));

export type RuntimeAction = "agent" | "connect" | "disconnect" | "forget" | "refresh" | "provider";
export type RuntimeNotice = { tone: "info" | "success" | "error"; text: string };

function errorText(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}

async function waitForModal(info: AgentInfo, attempts: number) {
  for (let index = 0; index < attempts; index += 1) {
    if ((await modalStatus(info)).connected) return true;
    if (index + 1 < attempts) await sleep(250);
  }
  return false;
}

async function modelsOrEmpty(info: AgentInfo) {
  try {
    return await listModels(info);
  } catch {
    return [] as ModelSpec[];
  }
}

export function useRuntimeController() {
  const inTauri = "__TAURI_INTERNALS__" in window;
  const [initialized, setInitialized] = useState(false);
  const [agent, setAgent] = useState<AgentInfo | null>(null);
  const [agentMessage, setAgentMessage] = useState("本地服务尚未启动");
  const [modalConnected, setModalConnected] = useState(false);
  const [modalMessage, setModalMessage] = useState("尚未连接");
  const [tokenId, setTokenId] = useState("");
  const [tokenSecret, setTokenSecret] = useState("");
  const [persistence, setPersistence] = useState<CredentialStatus>({ supported: false, stored: false });
  const [remember, setRemember] = useState(false);
  const [models, setModels] = useState<ModelSpec[]>([]);
  const [runtime, setRuntime] = useState<RuntimeCapabilities | null>(null);
  const [operations, setOperations] = useState<RuntimeAction[]>([]);
  const [notice, setNotice] = useState<RuntimeNotice | null>(null);
  const [diagnostics, setDiagnostics] = useState<AppDiagnostics | null>(null);
  const operationRef = useRef(new Set<RuntimeAction>());

  const begin = useCallback((action: RuntimeAction) => {
    if (operationRef.current.size) return false;
    operationRef.current.add(action);
    setOperations([...operationRef.current]);
    return true;
  }, []);

  const finish = useCallback((action: RuntimeAction) => {
    operationRef.current.delete(action);
    setOperations([...operationRef.current]);
  }, []);

  const applyReadyAgent = useCallback(async (info: AgentInfo, saved: CredentialStatus) => {
    await probeAgent(info);
    setAgent(info);
    setPersistence(saved);
    setRemember(saved.supported);
    setAgentMessage(`本地服务正常 · 127.0.0.1:${info.port}`);
    const connected = await waitForModal(info, saved.stored ? 20 : 1);
    setModalConnected(connected);
    setModalMessage(connected ? "已恢复 Modal 连接" : "等待连接");
    const [capabilityResult, modelResult] = await Promise.allSettled([
      getCapabilities(info),
      connected ? modelsOrEmpty(info) : Promise.resolve([] as ModelSpec[]),
    ]);
    if (capabilityResult.status === "fulfilled") setRuntime(capabilityResult.value);
    if (modelResult.status === "fulfilled") setModels(modelResult.value);
    if (capabilityResult.status === "rejected") {
      setNotice({ tone: "error", text: `本地预处理状态读取失败：${errorText(capabilityResult.reason)}` });
    } else {
      setNotice({ tone: "success", text: connected ? "本地预处理与 Modal 均已就绪" : "本地预处理服务已启动" });
    }
    return connected;
  }, []);

  useEffect(() => {
    if (!inTauri) {
      setInitialized(true);
      return;
    }
    let cancelled = false;
    async function initialize() {
      operationRef.current.add("agent");
      setOperations(["agent"]);
      setAgentMessage("正在启动本地服务…");
      try {
        const [status, saved] = await Promise.all([agentStatus(), credentialsStatus()]);
        const info = status.running ? status : await startAgent();
        if (cancelled) return;
        await applyReadyAgent(info, saved);
        setDiagnostics(await getAppDiagnostics().catch(() => null));
      } catch (error) {
        if (!cancelled) {
          const text = errorText(error);
          setAgentMessage(text);
          setNotice({ tone: "error", text: `本地服务启动失败：${text}` });
        }
      } finally {
        if (!cancelled) {
          operationRef.current.delete("agent");
          setOperations([]);
          setInitialized(true);
        }
      }
    }
    void initialize();
    return () => { cancelled = true; };
  }, [applyReadyAgent, inTauri]);

  useEffect(() => {
    if (!agent?.running) return;
    const info = agent;
    let cancelled = false;
    let timer = 0;
    async function heartbeat() {
      try {
        await probeAgent(info);
        if (!cancelled) setAgentMessage(`本地服务正常 · 127.0.0.1:${info.port}`);
      } catch (error) {
        if (!cancelled) setAgentMessage(`本地服务响应异常 · ${errorText(error)}`);
      } finally {
        if (!cancelled) timer = window.setTimeout(heartbeat, 15_000);
      }
    }
    timer = window.setTimeout(heartbeat, 15_000);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [agent]);

  const start = useCallback(async () => {
    if (!inTauri || !begin("agent")) return;
    try {
      const saved = await credentialsStatus();
      const info = await startAgent();
      await applyReadyAgent(info, saved);
      setDiagnostics(await getAppDiagnostics().catch(() => null));
    } catch (error) {
      setNotice({ tone: "error", text: errorText(error) });
    } finally {
      finish("agent");
    }
  }, [applyReadyAgent, begin, finish, inTauri]);

  const stop = useCallback(async () => {
    if (!inTauri || !begin("agent")) return;
    try {
      await stopAgent();
      setAgent({ running: false, port: null, session_token: null });
      setRuntime(null);
      setModels([]);
      setModalConnected(false);
      setAgentMessage("本地服务已停止");
      setModalMessage("本地服务已停止");
    } catch (error) {
      setNotice({ tone: "error", text: errorText(error) });
    } finally {
      finish("agent");
    }
  }, [begin, finish, inTauri]);

  const connect = useCallback(async () => {
    if (!agent?.running || !tokenId.trim() || !tokenSecret.trim() || !begin("connect")) return;
    const credentials = { token_id: tokenId.trim(), token_secret: tokenSecret.trim() };
    try {
      setModalMessage("正在验证 Modal 凭据…");
      await connectModal(agent, credentials);
      setModalConnected(true);
      let stored = persistence.stored;
      if (remember && persistence.supported) {
        await saveCredentials(credentials);
        stored = true;
      }
      setPersistence((current) => ({ ...current, stored }));
      setModels(await modelsOrEmpty(agent));
      setRuntime(await getCapabilities(agent));
      setTokenSecret("");
      setModalMessage(stored ? "已连接并安全保存凭据" : "当前会话已连接");
      setNotice({ tone: "success", text: "Modal 已连接" });
    } catch (error) {
      const text = errorText(error);
      setModalConnected(false);
      setModalMessage(text);
      setNotice({ tone: "error", text });
    } finally {
      finish("connect");
    }
  }, [agent, begin, finish, persistence.stored, persistence.supported, remember, tokenId, tokenSecret]);

  const disconnect = useCallback(async () => {
    if (!agent?.running || !begin("disconnect")) return;
    try {
      await disconnectModal(agent);
      setModalConnected(false);
      setModels([]);
      setModalMessage(persistence.stored ? "已断开；Windows 中仍保留凭据" : "已断开 Modal");
    } catch (error) {
      setNotice({ tone: "error", text: errorText(error) });
    } finally {
      finish("disconnect");
    }
  }, [agent, begin, finish, persistence.stored]);

  const forget = useCallback(async () => {
    if (!begin("forget")) return;
    try {
      await clearCredentials();
      if (agent?.running) await disconnectModal(agent);
      setPersistence((current) => ({ ...current, stored: false }));
      setModalConnected(false);
      setModels([]);
      setModalMessage("已删除保存的凭据");
      setNotice({ tone: "success", text: "Modal 凭据已删除" });
    } catch (error) {
      setNotice({ tone: "error", text: errorText(error) });
    } finally {
      finish("forget");
    }
  }, [agent, begin, finish]);

  const refresh = useCallback(async () => {
    if (!agent?.running || !begin("refresh")) return;
    try {
      const [capabilities, status] = await Promise.all([getCapabilities(agent), modalStatus(agent)]);
      setRuntime(capabilities);
      setModalConnected(status.connected);
      setModels(status.connected ? await modelsOrEmpty(agent) : []);
      setNotice({ tone: "success", text: "本地预处理与云端状态已刷新" });
    } catch (error) {
      setNotice({ tone: "error", text: errorText(error) });
    } finally {
      finish("refresh");
    }
  }, [agent, begin, finish]);

  const changePreprocessProvider = useCallback(async (provider: "cpu" | "gpu") => {
    if (!agent?.running || !begin("provider")) return;
    try {
      const preprocessing = await setPreprocessProvider(agent, provider);
      setRuntime((current) => current ? { ...current, preprocessing } : current);
      const fallback = preprocessing.fallback_reason ? `；${preprocessing.fallback_reason}` : "";
      setNotice({
        tone: preprocessing.provider === provider ? "success" : "info",
        text: `本地预处理已选择 ${provider.toUpperCase()}，实际执行 ${preprocessing.provider.toUpperCase()}${fallback}`,
      });
    } catch (error) {
      setNotice({ tone: "error", text: errorText(error) });
    } finally {
      finish("provider");
    }
  }, [agent, begin, finish]);

  const openDataDirectory = useCallback(async () => {
    try {
      await revealAppData();
    } catch (error) {
      setNotice({ tone: "error", text: errorText(error) });
    }
  }, []);

  return {
    inTauri,
    initialized,
    agent,
    agentMessage,
    modalConnected,
    modalMessage,
    tokenId,
    setTokenId,
    tokenSecret,
    setTokenSecret,
    persistence,
    remember,
    setRemember,
    models,
    runtime,
    operations,
    notice,
    diagnostics,
    dismissNotice: () => setNotice(null),
    start,
    stop,
    connect,
    disconnect,
    forget,
    refresh,
    changePreprocessProvider,
    openDataDirectory,
  };
}

export type RuntimeController = ReturnType<typeof useRuntimeController>;
