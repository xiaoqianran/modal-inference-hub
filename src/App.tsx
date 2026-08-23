import { useEffect, useState } from "react";
import "./App.css";
import {
  agentStatus,
  connectModal,
  disconnectModal,
  modalStatus,
  probeAgent,
  startAgent,
  stopAgent,
  type AgentInfo,
} from "./agent";

const layers = [
  ["桌面端", "Tauri 2 · Windows 原生外壳"],
  ["用户界面", "React · TypeScript"],
  ["本地代理", "Python · FastAPI · uv"],
  ["云端服务", "Modal 模型工作节点"],
] as const;

function App() {
  const [agent, setAgent] = useState<AgentInfo | null>(null);
  const [agentMessage, setAgentMessage] = useState("本地代理尚未启动");
  const [tokenId, setTokenId] = useState("");
  const [tokenSecret, setTokenSecret] = useState("");
  const [modalConnected, setModalConnected] = useState(false);
  const [modalMessage, setModalMessage] = useState("尚未连接");
  const inTauri = "__TAURI_INTERNALS__" in window;

  useEffect(() => {
    if (!inTauri) return;
    let cancelled = false;

    async function initializeAgent() {
      try {
        setAgentMessage("正在启动本地代理…");
        const status = await agentStatus();
        const info = status.running ? status : await startAgent();
        await probeAgent(info);
        const cloud = await modalStatus(info);
        if (cancelled) return;
        setAgent(info);
        setAgentMessage(`本地代理已就绪 · 127.0.0.1:${info.port}`);
        setModalConnected(cloud.connected);
        setModalMessage(cloud.connected ? "当前会话已连接" : "尚未连接");
      } catch (error) {
        if (cancelled) return;
        setAgentMessage(error instanceof Error ? error.message : String(error));
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
      const info = await startAgent();
      await probeAgent(info);
      setAgent(info);
      setAgentMessage(`本地代理已就绪 · 127.0.0.1:${info.port}`);
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
    try {
      setModalMessage("正在连接…");
      await connectModal(agent, { token_id: tokenId, token_secret: tokenSecret });
      setTokenSecret("");
      setModalConnected(true);
      setModalMessage("当前会话已连接");
    } catch (error) {
      setModalConnected(false);
      setModalMessage(error instanceof Error ? error.message : String(error));
    }
  }

  async function disconnect() {
    if (!agent?.running) return;
    await disconnectModal(agent);
    setModalConnected(false);
    setModalMessage("尚未连接");
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
          <p>凭据目前仅保存在本地代理的内存中，关闭应用后会自动清除。</p>
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
          <div className="form-actions">
            <span className={modalConnected ? "connected" : "muted"}>{modalMessage}</span>
            {modalConnected ? (
              <button onClick={disconnect}>断开连接</button>
            ) : (
              <button disabled={!agent?.running || !tokenId || !tokenSecret} onClick={connect}>连接云端</button>
            )}
          </div>
        </div>
      </section>
    </main>
  );
}

export default App;
