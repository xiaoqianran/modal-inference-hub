import { useEffect, useState } from "react";
import "./App.css";
import { agentStatus, probeAgent, startAgent, stopAgent, type AgentInfo } from "./agent";

const layers = [
  ["Desktop", "Tauri 2 · Windows shell"],
  ["UI", "React · TypeScript"],
  ["Agent", "Python · FastAPI · uv"],
  ["Cloud", "Modal workers"],
] as const;

function App() {
  const [agent, setAgent] = useState<AgentInfo | null>(null);
  const [message, setMessage] = useState("Local agent is stopped");
  const inTauri = "__TAURI_INTERNALS__" in window;

  useEffect(() => {
    if (!inTauri) return;
    agentStatus().then(setAgent).catch(() => setMessage("Unable to read agent status"));
  }, [inTauri]);

  async function start() {
    try {
      setMessage("Starting local agent…");
      const info = await startAgent();
      await probeAgent(info);
      setAgent(info);
      setMessage(`Agent ready on 127.0.0.1:${info.port}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    }
  }

  async function stop() {
    await stopAgent();
    setAgent({ running: false, port: null, session_token: null });
    setMessage("Local agent is stopped");
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
          <p>{inTauri ? message : "Open with `npm run tauri dev` to control the local agent."}</p>
        </div>
        {inTauri && (
          <button onClick={agent?.running ? stop : start}>{agent?.running ? "Stop agent" : "Start agent"}</button>
        )}
      </section>
    </main>
  );
}

export default App;
