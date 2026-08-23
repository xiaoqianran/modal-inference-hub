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
  ["Desktop", "Tauri 2 · Windows shell"],
  ["UI", "React · TypeScript"],
  ["Agent", "Python · FastAPI · uv"],
  ["Cloud", "Modal workers"],
] as const;

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

async function waitForModal(info: AgentInfo, attempts: number) {
  for (let i = 0; i < attempts; i += 1) {
    if ((await modalStatus(info)).connected) return true;
    if (i + 1 < attempts) await sleep(250);
  }
  return false;
}

function App() {
  const [agent, setAgent] = useState<AgentInfo | null>(null);
  const [agentMessage, setAgentMessage] = useState("Local agent is stopped");
  const [tokenId, setTokenId] = useState("");
  const [tokenSecret, setTokenSecret] = useState("");
  const [modalConnected, setModalConnected] = useState(false);
  const [modalMessage, setModalMessage] = useState("Not connected");
  const [persistence, setPersistence] = useState<CredentialStatus>({ supported: false, stored: false });
  const [remember, setRemember] = useState(false);
  const inTauri = "__TAURI_INTERNALS__" in window;

  useEffect(() => {
    if (!inTauri) return;
    Promise.all([agentStatus(), credentialsStatus()])
      .then(async ([info, saved]) => {
        setAgent(info);
        setPersistence(saved);
        setRemember(saved.supported);
        if (info.running) {
          setAgentMessage(`Agent ready on 127.0.0.1:${info.port}`);
          const connected = await waitForModal(info, 1);
          setModalConnected(connected);
          setModalMessage(connected ? "Connected" : "Not connected");
        }
      })
      .catch(() => setAgentMessage("Unable to read desktop status"));
  }, [inTauri]);

  async function start() {
    try {
      setAgentMessage("Starting local agent…");
      const saved = await credentialsStatus();
      const info = await startAgent();
      await probeAgent(info);
      setAgent(info);
      setPersistence(saved);
      setRemember(saved.supported);
      setAgentMessage(`Agent ready on 127.0.0.1:${info.port}`);
      const connected = await waitForModal(info, saved.stored ? 20 : 1);
      setModalConnected(connected);
      setModalMessage(connected ? "Connected · restored from Windows" : "Not connected");
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
    const credentials = { token_id: tokenId, token_secret: tokenSecret };
    try {
      setModalMessage("Connecting…");
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
          ? "Connected, but Windows credential save failed"
          : savedNow
            ? "Connected · saved in Windows"
            : "Connected for this session",
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
    setModalMessage(persistence.stored ? "Disconnected · saved credentials kept" : "Not connected");
  }

  async function forget() {
    await clearCredentials();
    if (agent?.running) await disconnectModal(agent);
    setPersistence({ ...persistence, stored: false });
    setModalConnected(false);
    setModalMessage("Saved credentials removed");
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
          <p>
            Token Secret is sent once to the localhost Agent. On Windows it can be stored in Credential Manager;
            it is never loaded back into the UI.
          </p>
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
          {persistence.supported && (
            <label className="remember">
              <input type="checkbox" checked={remember} onChange={(event) => setRemember(event.target.checked)} />
              Remember on this Windows PC
            </label>
          )}
          <div className="form-actions">
            <span className={modalConnected ? "connected" : "muted"}>{modalMessage}</span>
            <div className="buttons">
              {persistence.stored && <button className="secondary" onClick={forget}>Forget saved</button>}
              {modalConnected ? (
                <button onClick={disconnect}>Disconnect</button>
              ) : (
                <button disabled={!agent?.running || !tokenId || !tokenSecret} onClick={connect}>Connect</button>
              )}
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}

export default App;
