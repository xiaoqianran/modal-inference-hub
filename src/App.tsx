import { lazy, Suspense, useEffect, useState } from "react";
import "./App.css";
import {
  agentStatus,
  assetBlob,
  cancelJob,
  clearCredentials,
  connectModal,
  credentialsStatus,
  disconnectModal,
  getJob,
  listModels,
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
  type ModelSpec,
  type SamSelection,
} from "./agent";

const GlbViewer = lazy(() => import("./GlbViewer"));

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

  const [models, setModels] = useState<ModelSpec[]>([]);
  const [modelId, setModelId] = useState("fastsam3d-plus-plus");
  const [sourceFile, setSourceFile] = useState<File | null>(null);
  const [sourceUrl, setSourceUrl] = useState<string | null>(null);
  const [concept, setConcept] = useState("");
  const [selection, setSelection] = useState<SamSelection | null>(null);
  const [candidateId, setCandidateId] = useState<string | null>(null);
  const [canonical, setCanonical] = useState<CanonicalAsset | null>(null);
  const [canonicalUrl, setCanonicalUrl] = useState<string | null>(null);
  const [job, setJob] = useState<GenerationJob | null>(null);
  const [resultUrl, setResultUrl] = useState<string | null>(null);
  const [workflowMessage, setWorkflowMessage] = useState("导入图片并输入要提取的对象。");
  const [busy, setBusy] = useState(false);

  const inTauri = "__TAURI_INTERNALS__" in window;
  const selectedModel = models.find((model) => model.id === modelId) ?? models[0];
  const selectedProfile = selectedModel?.profiles[0];

  useEffect(() => {
    if (!inTauri) return;
    let cancelled = false;
    async function initializeAgent() {
      try {
        setAgentMessage("正在启动本地代理…");
        const [status, saved] = await Promise.all([agentStatus(), credentialsStatus()]);
        const info = status.running ? status : await startAgent();
        await probeAgent(info);
        const [connected, availableModels] = await Promise.all([
          waitForModal(info, saved.stored ? 20 : 1),
          listModels(info),
        ]);
        if (cancelled) return;
        setAgent(info);
        setModels(availableModels);
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
  useEffect(() => () => {
    if (resultUrl) URL.revokeObjectURL(resultUrl);
  }, [resultUrl]);

  async function start() {
    try {
      const saved = await credentialsStatus();
      const info = await startAgent();
      await probeAgent(info);
      const [connected, availableModels] = await Promise.all([
        waitForModal(info, saved.stored ? 20 : 1),
        listModels(info),
      ]);
      setAgent(info);
      setModels(availableModels);
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
      let saveFailed = false;
      if (remember && persistence.supported) {
        try {
          await saveCredentials(credentials);
          stored = true;
        } catch {
          saveFailed = true;
        }
      }
      setTokenSecret("");
      setPersistence({ ...persistence, stored });
      setModalConnected(true);
      setModalMessage(saveFailed ? "已连接，但保存 Windows 凭据失败" : stored ? "当前会话已连接 · 已保存到 Windows" : "当前会话已连接");
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

  function resetOutput() {
    setJob(null);
    setResultUrl(null);
  }

  function chooseImage(file: File | null) {
    setSourceFile(file);
    setSourceUrl(file ? URL.createObjectURL(file) : null);
    setSelection(null);
    setCandidateId(null);
    setCanonical(null);
    setCanonicalUrl(null);
    resetOutput();
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
      resetOutput();
      setWorkflowMessage(value.candidate_count ? `找到 ${value.candidate_count} 个候选，请选择目标。` : "没有找到候选对象，请换一个描述。");
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
      setCanonical(value);
      setCanonicalUrl(URL.createObjectURL(blob));
      resetOutput();
      setWorkflowMessage("Canonical RGBA 已确认，可以生成 3D。");
    } catch (error) {
      setWorkflowMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function generate() {
    if (!agent || !canonical || !selectedModel || !selectedProfile) return;
    try {
      setBusy(true);
      resetOutput();
      setWorkflowMessage(`已提交 ${selectedModel.name}，等待云端生成…`);
      let current = await submitGeneration(agent, canonical.canonical_path, selectedModel.id, selectedProfile.id);
      setJob(current);
      while (current.status === "running") {
        await sleep(1000);
        current = await getJob(agent, current.id);
        setJob(current);
      }
      if (current.status !== "succeeded" || !current.result) {
        setWorkflowMessage(current.error || `任务已结束：${current.status}`);
        return;
      }
      setWorkflowMessage("3D 已生成，正在加载预览…");
      const blob = await assetBlob(agent, current.result.artifact.path);
      setResultUrl(URL.createObjectURL(blob));
      setWorkflowMessage("3D 生成完成。");
    } catch (error) {
      setWorkflowMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function cancel() {
    if (!agent || !job || job.status !== "running") return;
    try {
      const current = await cancelJob(agent, job.id);
      setJob(current);
      setWorkflowMessage("任务已取消。");
    } catch (error) {
      setWorkflowMessage(error instanceof Error ? error.message : String(error));
    }
  }

  async function downloadResult() {
    if (!job?.result || !agent) return;
    try {
      let url = resultUrl;
      if (!url) {
        url = URL.createObjectURL(await assetBlob(agent, job.result.artifact.path));
        setResultUrl(url);
      }
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `modal-3d-${job.model}.glb`;
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
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
                <label className="upload"><input type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => chooseImage(event.target.files?.[0] ?? null)} />{sourceUrl ? "更换图片" : "选择图片"}</label>
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

                {selection && <div className="candidate-list">{selection.candidates.map((candidate) => <button key={candidate.candidate_id} className={candidateId === candidate.candidate_id ? "active" : ""} onClick={() => setCandidateId(candidate.candidate_id)}>#{candidate.rank + 1} · {(candidate.score * 100).toFixed(1)}%</button>)}</div>}
                <button className="primary full" disabled={busy || !candidateId} onClick={materialize}>确认对象</button>
              </div>

              <div className="panel">
                <div className="panel-title"><span>2</span><strong>选择模型并生成</strong></div>
                {resultUrl ? <Suspense fallback={<div className="glb-viewer"><span className="viewer-message">正在加载 3D 引擎…</span></div>}><GlbViewer url={resultUrl} /></Suspense> : (
                  <div className="canonical-preview">{canonicalUrl ? <img src={canonicalUrl} alt="Canonical RGBA" /> : <div>确认对象后，这里会显示标准透明 RGBA。</div>}</div>
                )}
                {canonical && !resultUrl && <div className="asset-meta"><span>Canonical RGBA</span><strong>{(canonical.canonical_bytes / 1024).toFixed(0)} KiB</strong></div>}

                <div className="model-options">
                  {models.map((model) => (
                    <button key={model.id} className={`model-option ${model.id === selectedModel?.id ? "active" : ""}`} disabled={busy} onClick={() => { setModelId(model.id); resetOutput(); }}>
                      <div><strong>{model.name}</strong><span>{model.description}</span></div>
                      <div className="model-meta"><span>Warm ~{model.warm_seconds.toFixed(model.warm_seconds < 10 ? 1 : 0)}s</span><span>{model.output === "textured" ? "纹理" : "几何"}</span></div>
                    </button>
                  ))}
                </div>

                {selectedProfile && <div className="profile-row"><span>Profile</span><strong>{selectedProfile.name}</strong></div>}
                {job?.status === "running" ? (
                  <div className="generation-actions"><button className="primary" disabled>云端生成中…</button><button className="danger" onClick={cancel}>取消</button></div>
                ) : (
                  <button className="primary full" disabled={busy || !canonical || !selectedModel} onClick={generate}>使用 {selectedModel?.name ?? "模型"} 生成 GLB</button>
                )}

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
