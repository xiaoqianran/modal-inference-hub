import { invoke } from "@tauri-apps/api/core";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  agentStatus,
  clearCredentials,
  connectModal,
  credentialsStatus,
  disconnectModal,
  getAppDiagnostics,
  getCapabilities,
  installLocalSam,
  listModels,
  migrateLocalSam,
  modalStatus,
  probeAgent,
  saveCredentials,
  revealAppData,
  setSamMode,
  startAgent,
  startLocalSam,
  stopAgent,
  uninstallLocalSam,
  type AgentInfo,
  type AppDiagnostics,
  type CredentialStatus,
  type ModelSpec,
  type RuntimeCapabilities,
  type SamMode,
} from "./agent";

const sleep = (milliseconds: number) => new Promise((resolve) => setTimeout(resolve, milliseconds));

export type RuntimeAction =
  | "agent"
  | "connect"
  | "disconnect"
  | "forget"
  | "refresh"
  | "mode"
  | "install"
  | "verify"
  | "migrate"
  | "uninstall";

export type RuntimeNotice = {
  tone: "info" | "success" | "error";
  text: string;
};

const CONNECTION_ACTIONS = new Set<RuntimeAction>(["connect", "disconnect", "forget"]);
const SAM_ACTIONS = new Set<RuntimeAction>(["mode", "install", "verify", "migrate", "uninstall"]);

function actionsConflict(active: RuntimeAction, next: RuntimeAction) {
  if (active === "agent" || next === "agent" || active === "refresh" || next === "refresh") return true;
  return (CONNECTION_ACTIONS.has(active) && CONNECTION_ACTIONS.has(next)) || (SAM_ACTIONS.has(active) && SAM_ACTIONS.has(next));
}

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

function progressLabel(runtime: RuntimeCapabilities | null) {
  const local = runtime?.sam.local;
  if (!local?.installing) return local?.reason ?? "";
  const eta = local.download_eta_seconds && local.download_eta_seconds > 0
    ? ` · 约 ${Math.ceil(local.download_eta_seconds / 60)} 分钟`
    : "";
  if (local.step === "checkpoint") {
    const total = local.download_total_bytes ?? local.checkpoint_bytes;
    const percent = total > 0 ? Math.min(100, ((local.downloaded_bytes ?? 0) / total) * 100) : 0;
    return `同步 SAM 3.1 模型 · ${percent.toFixed(1)}%${eta}`;
  }
  if (local.step === "dependencies") return `安装 Torch / CUDA 运行环境${eta}`;
  if (local.step === "health") return "加载模型并验证 GPU";
  return `下载 Local SAM 运行时${eta}`;
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
    if ([...operationRef.current].some((active) => actionsConflict(active, action))) return false;
    operationRef.current.add(action);
    setOperations([...operationRef.current]);
    return true;
  }, []);

  const finish = useCallback((action: RuntimeAction) => {
    operationRef.current.delete(action);
    setOperations([...operationRef.current]);
  }, []);

  const refreshCapabilities = useCallback(async (info: AgentInfo) => {
    const value = await getCapabilities(info);
    setRuntime(value);
    return value;
  }, []);

  const applyReadyAgent = useCallback(async (info: AgentInfo, saved: CredentialStatus) => {
    await probeAgent(info);
    setAgent(info);
    setPersistence(saved);
    setRemember(saved.supported);
    setAgentMessage(`本地服务正常 · 127.0.0.1:${info.port}`);
    const connected = await waitForModal(info, saved.stored ? 20 : 1);
    setModalConnected(connected);
    setModalMessage(connected ? "已从 Windows 凭据管理器恢复连接" : "等待连接");
    const [capabilityResult, modelResult] = await Promise.allSettled([
      getCapabilities(info),
      modelsOrEmpty(info),
    ]);
    if (capabilityResult.status === "fulfilled") setRuntime(capabilityResult.value);
    if (modelResult.status === "fulfilled") setModels(modelResult.value);
    if (capabilityResult.status === "rejected") {
      setNotice({ tone: "error", text: `本地服务已启动，但能力检测失败：${errorText(capabilityResult.reason)}` });
    } else if (connected && modelResult.status === "fulfilled" && !modelResult.value.length) {
      setNotice({ tone: "info", text: "Modal 已连接，但云端模型列表暂时不可用" });
    } else {
      setNotice({ tone: "success", text: connected ? "本地服务和 Modal 均已连接" : "本地服务已启动" });
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
      setOperations(["agent"]);
      operationRef.current.add("agent");
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
          setOperations([...operationRef.current]);
          setInitialized(true);
        }
      }
    }
    void initialize();
    return () => { cancelled = true; };
  }, [applyReadyAgent, inTauri]);

  const installing = Boolean(runtime?.sam.local.installing);
  useEffect(() => {
    if (!agent?.running || !installing) return;
    const info = agent;
    let cancelled = false;
    let timer = 0;
    async function poll() {
      try {
        const state = await refreshCapabilities(info);
        if (cancelled) return;
        if (state.sam.local.installing) {
          timer = window.setTimeout(poll, 1000);
        } else if (state.sam.local.error) {
          setNotice({ tone: "error", text: `Local SAM 安装失败：${state.sam.local.error}` });
        } else if (state.sam.local.ready) {
          setNotice({ tone: "success", text: `Local SAM 已就绪 · ${state.sam.local.health?.gpu ?? "NVIDIA GPU"}` });
        } else {
          setNotice({ tone: "info", text: state.sam.local.reason });
        }
      } catch (error) {
        if (!cancelled) {
          setNotice({ tone: "error", text: `无法读取安装进度：${errorText(error)}` });
          timer = window.setTimeout(poll, 2500);
        }
      }
    }
    timer = window.setTimeout(poll, 1000);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [agent, installing, refreshCapabilities]);

  const start = useCallback(async () => {
    if (!inTauri || !begin("agent")) return;
    try {
      setAgentMessage("正在启动本地服务…");
      const saved = await credentialsStatus();
      const info = await startAgent();
      await applyReadyAgent(info, saved);
      setDiagnostics(await getAppDiagnostics().catch(() => null));
    } catch (error) {
      const text = errorText(error);
      setAgentMessage(text);
      setNotice({ tone: "error", text });
    } finally {
      finish("agent");
    }
  }, [applyReadyAgent, begin, finish, inTauri]);

  const stop = useCallback(async () => {
    if (!inTauri || installing || !begin("agent")) return;
    try {
      await stopAgent();
      setAgent({ running: false, port: null, session_token: null });
      setRuntime(null);
      setModels([]);
      setModalConnected(false);
      setAgentMessage("本地服务已停止");
      setModalMessage("本地服务停止后，云端连接已关闭");
      setNotice({ tone: "info", text: "本地服务已停止，可随时重新启动" });
    } catch (error) {
      setNotice({ tone: "error", text: errorText(error) });
    } finally {
      finish("agent");
    }
  }, [begin, finish, inTauri, installing]);

  const connect = useCallback(async () => {
    if (!agent?.running || !tokenId.trim() || !tokenSecret.trim() || !begin("connect")) return;
    const credentials = { token_id: tokenId.trim(), token_secret: tokenSecret.trim() };
    try {
      setModalMessage("正在验证 Modal 凭据…");
      await connectModal(agent, credentials);
      setModalConnected(true);
      let stored = persistence.stored;
      let saveFailed = false;
      if (remember && persistence.supported) {
        try {
          await saveCredentials(credentials);
          stored = true;
        } catch {
          saveFailed = true;
        }
      }
      const [modelResult, capabilityResult] = await Promise.allSettled([
        listModels(agent),
        getCapabilities(agent),
      ]);
      if (modelResult.status === "fulfilled") setModels(modelResult.value);
      if (capabilityResult.status === "fulfilled") setRuntime(capabilityResult.value);
      setTokenSecret("");
      setPersistence((current) => ({ ...current, stored }));
      const capabilityFailed = modelResult.status === "rejected";
      const text = capabilityFailed
        ? `Modal 已连接，但模型列表暂时不可用：${errorText(modelResult.reason)}`
        : saveFailed
          ? "已连接，但 Windows 凭据保存失败"
          : stored
            ? "已连接并安全保存凭据"
            : "当前会话已连接";
      setModalMessage(text);
      setNotice({ tone: saveFailed || capabilityFailed ? "info" : "success", text });
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
      const text = persistence.stored ? "已断开；Windows 中仍保留凭据" : "已断开 Modal";
      setModalMessage(text);
      setNotice({ tone: "info", text });
      await refreshCapabilities(agent).catch(() => undefined);
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
      setModalMessage("已删除保存的凭据");
      setNotice({ tone: "success", text: "Modal 凭据已从 Windows 凭据管理器删除" });
    } catch (error) {
      setNotice({ tone: "error", text: errorText(error) });
    } finally {
      finish("forget");
    }
  }, [agent, begin, finish]);

  const refresh = useCallback(async () => {
    if (!agent?.running || !begin("refresh")) return;
    try {
      const [capabilities, status] = await Promise.all([
        getCapabilities(agent),
        modalStatus(agent),
      ]);
      setRuntime(capabilities);
      setModalConnected(status.connected);
      const availableModels = status.connected ? await modelsOrEmpty(agent) : [];
      if (status.connected) setModels(availableModels);
      setNotice({ tone: availableModels.length || !status.connected ? "success" : "info", text: status.connected && !availableModels.length ? "状态已刷新，但云端模型列表暂时不可用" : "连接、硬件和运行时状态已刷新" });
    } catch (error) {
      setNotice({ tone: "error", text: errorText(error) });
    } finally {
      finish("refresh");
    }
  }, [agent, begin, finish]);

  const changeSamMode = useCallback(async (mode: SamMode) => {
    if (!agent?.running || !begin("mode")) return;
    try {
      await setSamMode(agent, mode);
      const state = await refreshCapabilities(agent);
      const effective = state.sam.effective;
      const label = mode === "auto" ? `自动 → ${effective ?? "暂无可用服务"}` : mode === "cloud" ? "云端" : "本机";
      setNotice({ tone: effective ? "success" : "info", text: `SAM 模式已保存：${label}` });
    } catch (error) {
      setNotice({ tone: "error", text: errorText(error) });
    } finally {
      finish("mode");
    }
  }, [agent, begin, finish, refreshCapabilities]);

  const install = useCallback(async () => {
    if (!agent?.running || !begin("install")) return;
    try {
      const updating = runtime?.sam.local.update_available ?? false;
      await installLocalSam(agent);
      await refreshCapabilities(agent);
      setNotice({ tone: "info", text: `Local SAM 已开始后台${updating ? "更新" : "安装"}；可以关闭设置或继续使用云端。` });
    } catch (error) {
      setNotice({ tone: "error", text: errorText(error) });
    } finally {
      finish("install");
    }
  }, [agent, begin, finish, refreshCapabilities, runtime?.sam.local.update_available]);

  const verify = useCallback(async () => {
    if (!agent?.running || !begin("verify")) return;
    try {
      setNotice({ tone: "info", text: "正在加载模型并验证 NVIDIA GPU…" });
      await startLocalSam(agent);
      const state = await refreshCapabilities(agent);
      setNotice({
        tone: state.sam.local.ready ? "success" : "error",
        text: state.sam.local.ready ? `Local SAM 已就绪 · ${state.sam.local.health?.gpu ?? "NVIDIA GPU"}` : state.sam.local.reason,
      });
    } catch (error) {
      setNotice({ tone: "error", text: errorText(error) });
      await refreshCapabilities(agent).catch(() => undefined);
    } finally {
      finish("verify");
    }
  }, [agent, begin, finish, refreshCapabilities]);

  const migrate = useCallback(async () => {
    if (!agent?.running || installing || !begin("migrate")) return;
    try {
      const selected = await invoke<string | null>("choose_local_sam_directory");
      if (!selected) return;
      setNotice({ tone: "info", text: "正在迁移 Local SAM 数据，请保持客户端运行…" });
      await migrateLocalSam(agent, selected);
      await refreshCapabilities(agent);
      setNotice({ tone: "success", text: `Local SAM 已迁移到 ${selected}` });
    } catch (error) {
      setNotice({ tone: "error", text: errorText(error) });
    } finally {
      finish("migrate");
    }
  }, [agent, begin, finish, installing, refreshCapabilities]);

  const uninstall = useCallback(async () => {
    if (!agent?.running || installing || !begin("uninstall")) return;
    if (!window.confirm("卸载 Local SAM？运行时、CUDA 依赖和模型文件会被删除，项目与选择数据会保留。")) {
      finish("uninstall");
      return;
    }
    try {
      const result = await uninstallLocalSam(agent);
      await refreshCapabilities(agent);
      const released = result.released_bytes / 1024 / 1024 / 1024;
      setNotice({ tone: "success", text: `Local SAM 已卸载，释放 ${released.toFixed(2)} GiB；项目数据已保留。` });
    } catch (error) {
      setNotice({ tone: "error", text: errorText(error) });
    } finally {
      finish("uninstall");
    }
  }, [agent, begin, finish, installing, refreshCapabilities]);

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
    localProgress: useMemo(() => progressLabel(runtime), [runtime]),
    start,
    stop,
    connect,
    disconnect,
    forget,
    refresh,
    changeSamMode,
    install,
    verify,
    migrate,
    uninstall,
    openDataDirectory,
  };
}

export type RuntimeController = ReturnType<typeof useRuntimeController>;
