import "./App.css";

const layers = [
  ["Desktop", "Tauri 2 · Windows shell"],
  ["UI", "React · TypeScript"],
  ["Agent", "Python · FastAPI · uv"],
  ["Cloud", "Modal workers"],
] as const;

function App() {
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
        <span className="dot" />
        <div>
          <strong>Foundation ready</strong>
          <p>Next: local agent lifecycle, encrypted Modal credentials, then SAM 3.1 local/cloud routing.</p>
        </div>
      </section>
    </main>
  );
}

export default App;
