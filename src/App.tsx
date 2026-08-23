import { useEffect, useState } from "react";
import "./App.css";
import {
  agentStatus,
  clearCredentials,
  connectModal,
  credentialsStatus,
  disconnectModal,
  modalStatus,
  probeAgent,
  saveCredentials,
  startAgent,
  stopAgent,
  type AgentInfo,
  type CredentialStatus,
} from "./agent";

const layers = [
  ["桌面端", "Tauri 2 · Windows 原生外壳"],
  ["用户界面", "React · TypeScript"],
  ["本地代理", "Python · FastAPI · uv"],
  ["云端服务", "Modal 模型工作节点"],
] as const;

const sleep = (milliseconds: number) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function waitForModal(info: AgentInfo, attempts: number) {
  for (let index = 0; index < attempts; index += 1) {
    if ((await modalStatus(info)).connected) return true;
    if (index + 1 < attempts) await sleep(250);
  }
  return false;
}

function App() {
  const [agent, setAgent] = useState<AgentInfo | null>(null);
  const [agentMessage, setAgentMessage] = useState("本地代理尚未启动");
  const [tokenId, setTokenId] = useState("");
  const [tokenSecret, setTokenSecret] = useState("");
  const [modalConnected, setModalConnected] = useState(false);
  const [modalMessage, setModalMessage] = useState("尚未连接");
  const [persistence, setPersistence] = useState<CredentialStatus>({ supported: false, stored: false });
  const [remember, setRemember] = useState(false);
  const inTauri = "__TAURI_INTERNALS__" in window;

  useEffect(() => {
    if (!inTauri) return;
    let cancelled = false;

    async function initializeAgent() {
      try {
        setAgentMessage("正在启动本地代理…");
        const [status, saved] = await Promise.all([agentStatus(), credentialsStatus()]);
        const info = status.running ? status : await startAgent();
        await probeAgent(info);
        const connected = await waitForModal(info, saved.stored ? 20 : 1);
        if (cancelled) return;
        setAgent(info);
        setPersistence(saved);
        setRemember(saved.supported);
        setAgentMessage(`本地代理已就绪 · 127.0.0.1:${info.port}`);
        setModalConnected(connected);
        setModalMessage(connected ? "当前会话已连接 · 已从 Windows 恢复" : "尚未连接");
      } catch (error) {
        if (!cancelled) setAgentMessage(error instanceof Error ? error.message : String(error));
      }
    }

    void initializeAgent();
    return () => {
      cancelled = true;
    };
  }, [inTauri]);

  async function start() {
    try {
      setAgentMessage("正在启动本地代理…");
      const saved = await credentialsStatus();
      const info = await startAgent();
      await probeAgent(info);
      const connected = await waitForModal(info, saved.stored ? 20 : 1);
      setAgent(info);
      setPersistence(saved);
      setRemember(saved.supported);
      setAgentMessage(`本地代理已就绪 · 127.0.0.1:${info.port}`);
      setModalConnected(connected);
      setModalMessage(connected ? "当前会话已连接 · 已从 Windows 恢复" : "尚未连接");
    } catch (error) {
      setAgentMessage(error instanceof Error ? error.message : String(error));
    }
  }

  async function stop() {
    await stopAgent();
    setAgent({ running: false, port: null, session_token: null });
    setModalConnected(false);
    setAgentMessage("本地代理已停止");
    setModalMessage("尚未连接");
  }

  async function connect() {
    if (!agent?.running) return;
    const credentials = { token_id: tokenId, token_secret: tokenSecret };
    try {
      setModalMessage("正在连接…");
      await connectModal(agent, credentials);
      let stored = persistence.stored;
      let savedNow = false;
      let saveFailed = false;
      if (remember && persistence.supported) {
        try {
          await saveCredentials(credentials);
          stored = true;
          savedNow = true;
        } catch {
          saveFailed = true;
        }
      }
      setTokenSecret("");
      setPersistence({ ...persistence, stored });
      setModalConnected(true);
      setModalMessage(
        saveFailed
          ? "已连接，但保存 Windows 凭据失败"
          : savedNow
            ? "当前会话已连接 · 已保存到 Windows"
            : "当前会话已连接",
      );
    } catch (error) {
      setModalConnected(false);
      setModalMessage(error instanceof Error ? error.message : String(error));
    }
  }

  async function disconnect() {
    if (!agent?.running) return;
    await disconnectModal(agent);
    setModalConnected(false);
    setModalMessage(persistence.stored ? "已断开 · Windows 中仍保留凭据" : "尚未连接");
  }

  async function forget() {
    await clearCredentials();
    if (agent?.running) await disconnectModal(agent);
    setPersistence({ ...persistence, stored: false });
    setModalConnected(false);
    setModalMessage("已删除保存的凭据");
  }

  return (
    <main className="shell">
      <header>
        <div className="header-badge">
          <span className="badge-dot" />
          Windows 桌面端
        </div>
        <span className="eyebrow">modal-3D</span>
        <h1>三维创作客户端</h1>
        <p>连接本地算力与云端模型，让图像到三维资产的工作流更简单。</p>
      </header>

      <section className="grid" aria-label="系统架构状态">
        {layers.map(([name, detail]) => (
          <article key={name}>
            <span>{name}</span>
            <strong>{detail}</strong>
          </article>
        ))}
      </section>

      <section className="status">
        <span className={agent?.running ? "dot" : "dot idle"} />
        <div className="status-copy">
          <strong>{agent?.running ? "本地代理运行正常" : "正在初始化运行环境"}</strong>
          <p>{inTauri ? agentMessage : "请通过 `npm run desktop:dev` 打开桌面客户端。"}</p>
        </div>
        {inTauri && (
          <button onClick={agent?.running ? stop : start}>{agent?.running ? "停止代理" : "启动代理"}</button>
        )}
      </section>

      <section className="credentials">
        <div>
          <span className="eyebrow">云端服务</span>
          <h2>连接 Modal</h2>
          <p>凭据会先发送给本地代理；Windows 版本可将其安全保存到凭据管理器，且不会回显到界面。</p>
        </div>
        <div className="form">
          <label>
            令牌 ID
            <input value={tokenId} onChange={(event) => setTokenId(event.target.value)} autoComplete="off" placeholder="ak-…" />
          </label>
          <label>
            令牌密钥
            <input
              type="password"
              value={tokenSecret}
              onChange={(event) => setTokenSecret(event.target.value)}
              autoComplete="off"
              placeholder="••••••••••••"
            />
          </label>
          {persistence.supported && (
            <label className="remember">
              <input type="checkbox" checked={remember} onChange={(event) => setRemember(event.target.checked)} />
              在这台 Windows 电脑上记住
            </label>
          )}
          <div className="form-actions">
            <span className={modalConnected ? "connected" : "muted"}>{modalMessage}</span>
            <div className="buttons">
              {persistence.stored && <button className="secondary" onClick={forget}>删除已保存凭据</button>}
              {modalConnected ? (
                <button onClick={disconnect}>断开连接</button>
              ) : (
                <button disabled={!agent?.running || !tokenId || !tokenSecret} onClick={connect}>连接云端</button>
              )}
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}

export default App;
