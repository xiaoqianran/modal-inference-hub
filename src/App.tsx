import { useEffect, useState } from "react";
import "./App.css";
import {
  agentStatus,
  assetBlob,
  clearCredentials,
  connectModal,
  credentialsStatus,
  disconnectModal,
  getJob,
  materializeCandidate,
  modalStatus,
  probeAgent,
  saveCredentials,
  segmentImage,
  startAgent,
  stopAgent,
  submitGeneration,
  type AgentInfo,
  type CanonicalAsset,
  type CredentialStatus,
  type GenerationJob,
  type SamSelection,
} from "./agent";

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

  const [sourceFile, setSourceFile] = useState<File | null>(null);
  const [sourceUrl, setSourceUrl] = useState<string | null>(null);
  const [concept, setConcept] = useState("");
  const [selection, setSelection] = useState<SamSelection | null>(null);
  const [candidateId, setCandidateId] = useState<string | null>(null);
  const [canonical, setCanonical] = useState<CanonicalAsset | null>(null);
  const [canonicalUrl, setCanonicalUrl] = useState<string | null>(null);
  const [job, setJob] = useState<GenerationJob | null>(null);
  const [workflowMessage, setWorkflowMessage] = useState("导入图片并输入要提取的对象。");
  const [busy, setBusy] = useState(false);

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

  useEffect(() => () => {
    if (sourceUrl) URL.revokeObjectURL(sourceUrl);
  }, [sourceUrl]);

  useEffect(() => () => {
    if (canonicalUrl) URL.revokeObjectURL(canonicalUrl);
  }, [canonicalUrl]);

  async function start() {
    try {
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
      setModalMessage(saveFailed ? "已连接，但保存 Windows 凭据失败" : savedNow ? "当前会话已连接 · 已保存到 Windows" : "当前会话已连接");
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

  function chooseImage(file: File | null) {
    if (sourceUrl) URL.revokeObjectURL(sourceUrl);
    if (canonicalUrl) URL.revokeObjectURL(canonicalUrl);
    setSourceFile(file);
    setSourceUrl(file ? URL.createObjectURL(file) : null);
    setSelection(null);
    setCandidateId(null);
    setCanonical(null);
    setCanonicalUrl(null);
    setJob(null);
    setWorkflowMessage(file ? "图片已就绪。输入对象名称后开始分割。" : "导入图片并输入要提取的对象。");
  }

  async function segment() {
    if (!agent || !sourceFile || !concept.trim()) return;
    try {
      setBusy(true);
      setWorkflowMessage("Cloud SAM 正在识别对象…");
      const value = await segmentImage(agent, sourceFile, concept.trim());
      setSelection(value);
      setCandidateId(value.candidates[0]?.candidate_id ?? null);
      setCanonical(null);
      setCanonicalUrl(null);
      setJob(null);
      setWorkflowMessage(value.candidate_count ? `找到 ${value.candidate_count} 个候选，请选择目标。` : "没有找到候选对象，请换一个描述。 ");
    } catch (error) {
      setWorkflowMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function materialize() {
    if (!agent || !selection || !candidateId) return;
    try {
      setBusy(true);
      setWorkflowMessage("正在生成标准 Canonical RGBA…");
      const value = await materializeCandidate(agent, selection, candidateId);
      const blob = await assetBlob(agent, value.canonical_path);
      if (canonicalUrl) URL.revokeObjectURL(canonicalUrl);
      setCanonical(value);
      setCanonicalUrl(URL.createObjectURL(blob));
      setJob(null);
      setWorkflowMessage("Canonical RGBA 已确认，可以生成 3D。");
    } catch (error) {
      setWorkflowMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function generate() {
    if (!agent || !canonical) return;
    try {
      setBusy(true);
      setWorkflowMessage("已提交 FastSAM3D++，等待云端生成…");
      let current = await submitGeneration(agent, canonical.canonical_path);
      setJob(current);
      while (current.status === "running") {
        await sleep(1000);
        current = await getJob(agent, current.id);
        setJob(current);
      }
      if (current.status === "succeeded") {
        setWorkflowMessage("3D 生成完成。");
      } else {
        setWorkflowMessage(current.error || `任务已结束：${current.status}`);
      }
    } catch (error) {
      setWorkflowMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function downloadResult() {
    if (!agent || !job?.result) return;
    try {
      const blob = await assetBlob(agent, job.result.artifact.path);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `modal-3d-${job.model}.glb`;
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (error) {
      setWorkflowMessage(error instanceof Error ? error.message : String(error));
    }
  }

  return (
    <main className="shell">
      <header>
        <div className="header-badge"><span className="badge-dot" />Image → 3D</div>
        <span className="eyebrow">modal-3D</span>
        <h1>三维创作客户端</h1>
        <p>选择对象，生成标准透明资产，再交给云端 GPU 生成 GLB。</p>
      </header>

      <section className="status">
        <span className={agent?.running ? "dot" : "dot idle"} />
        <div className="status-copy">
          <strong>{agent?.running ? "本地代理运行正常" : "正在初始化运行环境"}</strong>
          <p>{inTauri ? agentMessage : "请通过 `npm run desktop:dev` 打开桌面客户端。"}</p>
        </div>
        {inTauri && <button onClick={agent?.running ? stop : start}>{agent?.running ? "停止代理" : "启动代理"}</button>}
      </section>

      {!modalConnected && (
        <section className="credentials">
          <div>
            <span className="eyebrow">云端服务</span>
            <h2>连接 Modal</h2>
            <p>凭据只交给本地 Agent；Windows 可使用凭据管理器安全保存。</p>
          </div>
          <div className="form">
            <label>令牌 ID<input value={tokenId} onChange={(event) => setTokenId(event.target.value)} autoComplete="off" placeholder="ak-…" /></label>
            <label>令牌密钥<input type="password" value={tokenSecret} onChange={(event) => setTokenSecret(event.target.value)} autoComplete="off" placeholder="••••••••••••" /></label>
            {persistence.supported && <label className="remember"><input type="checkbox" checked={remember} onChange={(event) => setRemember(event.target.checked)} />在这台 Windows 电脑上记住</label>}
            <div className="form-actions">
              <span className="muted">{modalMessage}</span>
              <button disabled={!agent?.running || !tokenId || !tokenSecret} onClick={connect}>连接云端</button>
            </div>
          </div>
        </section>
      )}

      {modalConnected && (
        <>
          <section className="cloud-bar">
            <span className="connected">● Modal 已连接</span>
            <div className="buttons">
              {persistence.stored && <button className="secondary" onClick={forget}>删除已保存凭据</button>}
              <button className="secondary" onClick={disconnect}>断开</button>
            </div>
          </section>

          <section className="workspace">
            <div className="workspace-head">
              <div><span className="eyebrow">创建</span><h2>从图片生成 3D</h2></div>
              <span className="workflow-message">{workflowMessage}</span>
            </div>

            <div className="workflow-grid">
              <div className="panel">
                <div className="panel-title"><span>1</span><strong>选择对象</strong></div>
                <label className="upload">
                  <input type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => chooseImage(event.target.files?.[0] ?? null)} />
                  {sourceUrl ? "更换图片" : "选择图片"}
                </label>
                <div className="concept-row">
                  <input value={concept} onChange={(event) => setConcept(event.target.value)} placeholder="例如：cup、chair、plant" onKeyDown={(event) => { if (event.key === "Enter") void segment(); }} />
                  <button disabled={busy || !sourceFile || !concept.trim()} onClick={segment}>识别</button>
                </div>

                {sourceUrl && (
                  <div className="image-stage" style={selection ? { aspectRatio: `${selection.image_size[0]} / ${selection.image_size[1]}` } : undefined}>
                    <img src={sourceUrl} alt="源图片" />
                    {selection?.candidates.map((candidate) => {
                      const [x0, y0, x1, y1] = candidate.model_bbox_xyxy_norm;
                      return <button key={candidate.candidate_id} className={`candidate ${candidateId === candidate.candidate_id ? "selected" : ""}`} style={{ left: `${x0 * 100}%`, top: `${y0 * 100}%`, width: `${(x1 - x0) * 100}%`, height: `${(y1 - y0) * 100}%` }} onClick={() => setCandidateId(candidate.candidate_id)} title={`score ${candidate.score.toFixed(3)}`} />;
                    })}
                  </div>
                )}

                {selection && (
                  <div className="candidate-list">
                    {selection.candidates.map((candidate) => <button key={candidate.candidate_id} className={candidateId === candidate.candidate_id ? "active" : ""} onClick={() => setCandidateId(candidate.candidate_id)}>#{candidate.rank + 1} · {(candidate.score * 100).toFixed(1)}%</button>)}
                  </div>
                )}
                <button className="primary full" disabled={busy || !candidateId} onClick={materialize}>确认对象</button>
              </div>

              <div className="panel">
                <div className="panel-title"><span>2</span><strong>生成 3D</strong></div>
                <div className="canonical-preview">
                  {canonicalUrl ? <img src={canonicalUrl} alt="Canonical RGBA" /> : <div>确认对象后，这里会显示标准透明 RGBA。</div>}
                </div>
                {canonical && <div className="asset-meta"><span>Canonical RGBA</span><strong>{(canonical.canonical_bytes / 1024).toFixed(0)} KiB</strong></div>}
                <div className="model-card"><div><strong>FastSAM3D++</strong><span>当前 MVP · 快速几何生成</span></div><span className="model-badge">L40S</span></div>
                <button className="primary full" disabled={busy || !canonical} onClick={generate}>{job?.status === "running" ? "云端生成中…" : "生成 GLB"}</button>

                {job?.result && (
                  <div className="result-card">
                    <div><span>生成完成</span><strong>{(job.result.artifact.bytes / 1024 / 1024).toFixed(2)} MiB</strong></div>
                    <div className="result-timing">
                      {job.result.timing.inference_s !== undefined && <span>推理 {job.result.timing.inference_s.toFixed(2)}s</span>}
                      {job.result.timing.load_s !== undefined && <span>加载 {job.result.timing.load_s.toFixed(2)}s</span>}
                    </div>
                    <button className="primary" onClick={downloadResult}>下载 GLB</button>
                  </div>
                )}
              </div>
            </div>
          </section>
        </>
      )}
    </main>
  );
}

export default App;
