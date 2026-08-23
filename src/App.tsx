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
  ["Desktop", "Tauri 2 · Windows shell"],
  ["UI", "React · TypeScript"],
  ["Agent", "Python · FastAPI · uv"],
  ["Cloud", "Modal workers"],
] as const;

function App() {
  const [agent, setAgent] = useState<AgentInfo | null>(null);
  const [agentMessage, setAgentMessage] = useState("Local agent is stopped");
  const [tokenId, setTokenId] = useState("");
  const [tokenSecret, setTokenSecret] = useState("");
  const [modalConnected, setModalConnected] = useState(false);
  const [modalMessage, setModalMessage] = useState("Not connected");
  const inTauri = "__TAURI_INTERNALS__" in window;

  useEffect(() => {
    if (!inTauri) return;
    agentStatus()
      .then(async (info) => {
        setAgent(info);
        if (info.running) {
          setAgentMessage(`Agent ready on 127.0.0.1:${info.port}`);
          const status = await modalStatus(info);
          setModalConnected(status.connected);
          setModalMessage(status.connected ? "Connected for this session" : "Not connected");
        }
      })
      .catch(() => setAgentMessage("Unable to read agent status"));
  }, [inTauri]);

  async function start() {
    try {
      setAgentMessage("Starting local agent…");
      const info = await startAgent();
      await probeAgent(info);
      setAgent(info);
      setAgentMessage(`Agent ready on 127.0.0.1:${info.port}`);
    } catch (error) {
      setAgentMessage(error instanceof Error ? error.message : String(error));
    }
  }

  async function stop() {
    await stopAgent();
    setAgent({ running: false, port: null, session_token: null });
    setModalConnected(false);
    setAgentMessage("Local agent is stopped");
    setModalMessage("Not connected");
  }

  async function connect() {
    if (!agent?.running) return;
    try {
      setModalMessage("Connecting…");
      await connectModal(agent, { token_id: tokenId, token_secret: tokenSecret });
      setTokenSecret("");
      setModalConnected(true);
      setModalMessage("Connected for this session");
    } catch (error) {
      setModalConnected(false);
      setModalMessage(error instanceof Error ? error.message : String(error));
    }
  }

  async function disconnect() {
    if (!agent?.running) return;
    await disconnectModal(agent);
    setModalConnected(false);
    setModalMessage("Not connected");
  }

  return (
    <main className="shell">
      <header>
        <span className="eyebrow">modal-3D</span>
        <h1>Client</h1>
        <p>Hybrid local/cloud image-to-3D desktop client.</p>
      </header>

      <section className="grid" aria-label="Architecture status">
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
          <strong>{agent?.running ? "Local agent ready" : "Foundation ready"}</strong>
          <p>{inTauri ? agentMessage : "Open with `npm run tauri dev` to control the local agent."}</p>
        </div>
        {inTauri && (
          <button onClick={agent?.running ? stop : start}>{agent?.running ? "Stop agent" : "Start agent"}</button>
        )}
      </section>

      <section className="credentials">
        <div>
          <span className="eyebrow">Cloud</span>
          <h2>Modal credentials</h2>
          <p>Stored in Agent memory only for now. Encrypted persistence comes next.</p>
        </div>
        <div className="form">
          <label>
            Token ID
            <input value={tokenId} onChange={(event) => setTokenId(event.target.value)} autoComplete="off" placeholder="ak-…" />
          </label>
          <label>
            Token Secret
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
              <button onClick={disconnect}>Disconnect</button>
            ) : (
              <button disabled={!agent?.running || !tokenId || !tokenSecret} onClick={connect}>Connect</button>
            )}
          </div>
        </div>
      </section>
    </main>
  );
}

export default App;
