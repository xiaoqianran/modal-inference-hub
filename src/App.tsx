import { FormEvent, lazy, Suspense, useCallback, useEffect, useMemo, useState } from "react";
import {
  Batch,
  Candidate,
  Deployment,
  DeploymentPlan,
  Experiment,
  HubApi,
  isExperimentActive,
  parseModalCredentials,
  Provider,
  ProviderModel,
} from "./api";
import "./App.css";

const GlbViewer = lazy(() =>
  import("./GlbViewer").then((module) => ({ default: module.GlbViewer })),
);

function ArtifactImage({
  api,
  experiment,
  candidate,
}: {
  api: HubApi;
  experiment: string;
  candidate: Candidate;
}) {
  const [url, setUrl] = useState<string>();
  useEffect(() => {
    if (candidate.job.state !== "succeeded") return;
    let alive = true;
    let objectUrl: string | undefined;
    api
      .artifactUrl(`/api/experiments/${experiment}/candidates/${candidate.id}/artifact`)
      .then((value) => {
        objectUrl = value;
        if (alive) setUrl(value);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [api, experiment, candidate.id, candidate.job.state]);
  return url ? <img src={url} alt={`候选 ${candidate.ordinal}`} /> : <span>{candidate.job.state}</span>;
}

function modelId(models: ProviderModel[], fallback: string) {
  return models.find((item) => item.status !== "disabled")?.id ?? fallback;
}

export default function App() {
  const [api, setApi] = useState<HubApi>();
  const [providers, setProviders] = useState<Provider[]>([]);
  const [experiments, setExperiments] = useState<Experiment[]>([]);
  const [batches, setBatches] = useState<Batch[]>([]);
  const [selectedId, setSelectedId] = useState<string>();
  const [current, setCurrent] = useState<Experiment>();
  const [prompt, setPrompt] = useState("");
  const [count, setCount] = useState(4);
  const [imageModel, setImageModel] = useState("");
  const [assetModel, setAssetModel] = useState("");
  const [profile, setProfile] = useState("recommended");
  const [credentialPaste, setCredentialPaste] = useState("");
  const [deploymentPlan, setDeploymentPlan] = useState<DeploymentPlan>();
  const [deployment, setDeployment] = useState<Deployment>();
  const [deploymentCredential, setDeploymentCredential] = useState("");
  const [batchMode, setBatchMode] = useState<"prompts" | "images">();
  const [batchPrompts, setBatchPrompts] = useState("");
  const [batchFiles, setBatchFiles] = useState<File[]>([]);
  const [batch, setBatch] = useState<Batch>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [glbUrl, setGlbUrl] = useState<string>();

  const refreshList = useCallback(async (client: HubApi) => {
    const [
      { providers: nextProviders },
      { experiments: nextExperiments },
      { batches: nextBatches },
    ] = await Promise.all([
      client.providers(),
      client.experiments(),
      client.batches(),
    ]);
    setProviders(nextProviders);
    setExperiments(nextExperiments);
    setBatches(nextBatches);
    const image = nextProviders.find((item) => item.id === "modal-2d");
    const asset = nextProviders.find((item) => item.id === "modal-3d");
    setImageModel((value) => value || modelId(image?.models ?? [], "sana-sprint-1.6b"));
    setAssetModel((value) => value || modelId(asset?.models ?? [], ""));
  }, []);

  useEffect(() => {
    HubApi.connect()
      .then(async (client) => {
        setApi(client);
        await refreshList(client);
      })
      .catch((cause) => setError(String(cause)));
  }, [refreshList]);

  useEffect(() => {
    if (!api || !selectedId) return;
    let cancelled = false;
    const load = async () => {
      try {
        const value = await api.experiment(selectedId);
        if (!cancelled) {
          setCurrent(value);
          setExperiments((items) => items.map((item) => (item.id === value.id ? value : item)));
        }
      } catch (cause) {
        if (!cancelled) setError(String(cause));
      }
    };
    void load();
    const timer = window.setInterval(() => {
      if (current ? isExperimentActive(current.phase) : true) void load();
    }, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [api, selectedId, current?.phase]);

  useEffect(() => {
    if (!api || !current || current.phase !== "complete") {
      setGlbUrl((old) => {
        if (old) URL.revokeObjectURL(old);
        return undefined;
      });
      return;
    }
    let alive = true;
    let url: string | undefined;
    api.artifactUrl(`/api/experiments/${current.id}/artifact`).then((value) => {
      url = value;
      if (alive) setGlbUrl(value);
    });
    return () => {
      alive = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [api, current?.id, current?.phase]);

  useEffect(() => {
    if (!api || !deployment || !["queued", "running"].includes(deployment.state)) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const value = await api.deployment(deployment.id);
        if (!cancelled) setDeployment(value);
        if (value.state === "succeeded" && !cancelled) await refreshList(api);
      } catch (cause) {
        if (!cancelled) setError(String(cause));
      }
    };
    const timer = window.setInterval(() => void poll(), 1500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [api, deployment?.id, deployment?.state, refreshList]);

  useEffect(() => {
    if (!api || !batch || batch.state !== "running") return;
    let cancelled = false;
    const poll = async () => {
      try {
        const value = await api.batch(batch.id);
        if (!cancelled) {
          setBatch(value);
          setBatches((items) => items.map((item) => (item.id === value.id ? value : item)));
        }
        if (!cancelled && value.state !== "running") await refreshList(api);
      } catch (cause) {
        if (!cancelled) setError(String(cause));
      }
    };
    const timer = window.setInterval(() => void poll(), 1500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [api, batch?.id, batch?.state, refreshList]);

  const imageProvider = providers.find((item) => item.id === "modal-2d");
  const assetProvider = providers.find((item) => item.id === "modal-3d");
  const profiles = useMemo(
    () => assetProvider?.models.find((item) => item.id === assetModel)?.profiles ?? [],
    [assetProvider, assetModel],
  );
  const promptItems = useMemo(
    () => Array.from(new Set(batchPrompts.split(/\r?\n/).map((item) => item.trim()).filter(Boolean))),
    [batchPrompts],
  );

  const create = async (event: FormEvent) => {
    event.preventDefault();
    if (!api) return;
    setBusy(true);
    setError(undefined);
    try {
      const value = await api.create({
        prompt,
        candidate_count: count,
        image_model: imageModel,
        seed: 42,
      });
      setExperiments((items) => [value, ...items]);
      setSelectedId(value.id);
      setCurrent(value);
      setPrompt("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  const connectProviders = async (event: FormEvent) => {
    event.preventDefault();
    if (!api) return;
    setBusy(true);
    setError(undefined);
    try {
      const { tokenId, tokenSecret } = parseModalCredentials(credentialPaste);
      setCredentialPaste("");
      const targets = providers.filter((item) => item.reachable && !item.connected);
      const results = await Promise.allSettled(
        targets.map((item) => api.connectProvider(item.id, tokenId, tokenSecret)),
      );
      await refreshList(api);
      const failures = results.flatMap((item, index) =>
        item.status === "rejected"
          ? [`${targets[index].id}: ${item.reason instanceof Error ? item.reason.message : String(item.reason)}`]
          : [],
      );
      if (failures.length) throw new Error(failures.join("；"));
    } catch (cause) {
      setCredentialPaste("");
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  const openDeployment = async (provider: Provider["id"]) => {
    if (!api) return;
    setBusy(true);
    setError(undefined);
    try {
      setDeployment(undefined);
      setDeploymentPlan(await api.deploymentPlan(provider));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  const startDeployment = async (event: FormEvent) => {
    event.preventDefault();
    if (!api || !deploymentPlan) return;
    setBusy(true);
    setError(undefined);
    try {
      const { tokenId, tokenSecret } = parseModalCredentials(deploymentCredential);
      setDeploymentCredential("");
      setDeployment(
        await api.startDeployment(deploymentPlan.provider, tokenId, tokenSecret),
      );
    } catch (cause) {
      setDeploymentCredential("");
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  const createBatch = async (event: FormEvent) => {
    event.preventDefault();
    if (!api || !batchMode) return;
    setBusy(true);
    setError(undefined);
    try {
      let value: Batch;
      if (batchMode === "prompts") {
        value = await api.createPromptBatch({
            prompts: promptItems,
            candidate_count: count,
            image_model: imageModel,
            seed: 42,
          });
      } else {
        const sources = [];
        for (const file of batchFiles) sources.push(await api.ingestImage(file));
        value = await api.createImageBatch({ sources, model: assetModel, profile, seed: 42 });
      }
      setBatch(value);
      setBatches((items) => [value, ...items]);
      setBatchPrompts("");
      setBatchFiles([]);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  const openExperiment = (id: string) => {
    setBatchMode(undefined);
    setBatch(undefined);
    setSelectedId(id);
  };

  const openBatch = async (id: string, kind: Batch["kind"]) => {
    if (!api) return;
    setSelectedId(undefined);
    setCurrent(undefined);
    setBatchMode(kind);
    setBusy(true);
    try {
      setBatch(await api.batch(id));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  const resumeBatch = async () => {
    if (!api || !batch) return;
    setBusy(true);
    try {
      setBatch(await api.resumeBatch(batch.id));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  const downloadDirect = async (runId: string) => {
    if (!api) return;
    try {
      const url = await api.artifactUrl(`/api/direct-images/${runId}/artifact`);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${runId}.glb`;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  const choose = async (candidateId: string) => {
    if (!api || !current) return;
    setBusy(true);
    try {
      setCurrent(await api.select(current.id, candidateId));
    } catch (cause) {
      setError(String(cause));
    } finally {
      setBusy(false);
    }
  };

  const generate = async () => {
    if (!api || !current) return;
    setBusy(true);
    try {
      setCurrent(await api.generate3d(current.id, { model: assetModel, profile, seed: 42 }));
    } catch (cause) {
      setError(String(cause));
    } finally {
      setBusy(false);
    }
  };

  const resume = async () => {
    if (!api || !current) return;
    setBusy(true);
    try {
      setCurrent(await api.resume(current.id));
    } catch (cause) {
      setError(String(cause));
    } finally {
      setBusy(false);
    }
  };

  const canResume =
    current?.asset3d?.job.state === "uncertain" ||
    current?.asset3d?.job.state === "connection_required" ||
    current?.image.candidates.some((item) => item.job.state === "uncertain");

  return (
    <main className="shell">
      <aside>
        <header>
          <div className="mark">MI</div>
          <div><strong>Modal Inference Hub</strong><small>实验导向工作台</small></div>
        </header>
        <div className="providers">
          {[imageProvider, assetProvider].map((provider) => (
            <div key={provider?.id ?? "sidecar"}>
              <span className={provider?.connected ? "ok" : ""}>
                <i /> {provider?.id ?? "sidecar"} · {provider?.connected ? "已连接" : "未就绪"}
              </span>
              {provider && <button onClick={() => openDeployment(provider.id)}>部署</button>}
            </div>
          ))}
        </div>
        <nav>
          {batches.map((item) => (
            <button
              key={item.id}
              className={batch?.id === item.id ? "selected" : ""}
              onClick={() => openBatch(item.id, item.kind)}
            >
              <strong>{item.kind === "prompts" ? "提示词批次" : "图片批次"} · {item.summary.total} 项</strong>
              <small>{item.state}</small>
            </button>
          ))}
          {experiments.map((item) => (
            <button
              key={item.id}
              className={selectedId === item.id ? "selected" : ""}
              onClick={() => openExperiment(item.id)}
            >
              <strong>{item.title}</strong>
              <small>{item.phase}</small>
            </button>
          ))}
        </nav>
      </aside>

      <section className="workspace">
        <div className="topbar">
          <div><small>EXPERIMENT-ORIENTED MODULAR MONOLITH</small><h1>Text → 候选图 → 人工选择 → 3D</h1></div>
          <div className="legend">
            <span>Hub 只拥有实验/批次</span><span>Sidecar 拥有执行</span>
            <button onClick={() => { setBatchMode("prompts"); setBatch(undefined); }}>批量处理</button>
          </div>
        </div>

        {deploymentPlan && (
          <form className="deployment-panel" onSubmit={startDeployment}>
            <div>
              <span className="eyebrow">PROVIDER-OWNED DEPLOYMENT</span>
              <h2>{deploymentPlan.provider} 自动部署</h2>
              <p>将部署：{deploymentPlan.apps.join("、")}。具体顺序与校验由 Provider 源码拥有。</p>
            </div>
            {deployment ? (
              <div className={`deployment-state ${deployment.state}`}>
                <strong>{deployment.state}</strong>
                <span>{deployment.stage}</span>
                <small>{deployment.events[deployment.events.length - 1]?.message || deployment.error}</small>
              </div>
            ) : (
              <div className="deployment-confirm">
                <input type="password" value={deploymentCredential} onChange={(event) => setDeploymentCredential(event.target.value)} autoComplete="off" placeholder="再次粘贴 Modal Token 命令" required />
                <button className="primary" disabled={busy || !deploymentCredential.trim()}>确认部署 {deploymentPlan.apps.length} 个 App</button>
              </div>
            )}
            <button type="button" className="close" onClick={() => { setDeploymentPlan(undefined); setDeployment(undefined); setDeploymentCredential(""); }}>关闭</button>
          </form>
        )}

        {providers.some((item) => item.reachable && !item.connected) ? (
          <form className="new-experiment connection" onSubmit={connectProviders}>
            <span className="eyebrow">SIDECAR CONNECTION</span>
            <h2>连接 Modal Provider</h2>
            <p>可直接粘贴 modal token set 命令，或粘贴 ID 与 Secret。这里只解析凭据，不执行命令；Hub 不保存凭据。</p>
            <div className="form-row">
              <label>Modal Token 命令或凭据<input type="password" value={credentialPaste} onChange={(event) => setCredentialPaste(event.target.value)} autoComplete="off" required /></label>
              <button className="primary" disabled={busy || !credentialPaste.trim()}>自动识别并连接</button>
            </div>
          </form>
        ) : batchMode ? (
          <form className="batch-panel" onSubmit={createBatch}>
            <header>
              <div><span className="eyebrow">BOUNDED BATCH</span><h2>批量提示词与图片</h2></div>
              <div className="batch-tabs">
                <button type="button" className={batchMode === "prompts" ? "selected" : ""} onClick={() => { setBatchMode("prompts"); setBatch(undefined); }}>提示词批次</button>
                <button type="button" className={batchMode === "images" ? "selected" : ""} onClick={() => { setBatchMode("images"); setBatch(undefined); }}>图片批次</button>
                <button type="button" onClick={() => setBatchMode(undefined)}>关闭</button>
              </div>
            </header>
            {batch ? (
              <div className="batch-result">
                <div className="batch-summary"><strong>{batch.state}</strong><span>{batch.summary.total} 项</span><small>{Object.entries(batch.summary).filter(([key]) => key !== "total").map(([key, value]) => `${key} ${value}`).join(" · ")}</small></div>
                <div className="batch-items">
                  {batch.items.map((item) => (
                    <div key={item.id}>
                      <span>{String(item.source.prompt ?? item.source.name ?? item.id)}</span>
                      <small>{item.state}{item.error ? ` · ${item.error}` : ""}</small>
                      {item.target.kind === "experiment" && item.state === "awaiting_review" && <button type="button" onClick={() => openExperiment(item.target.id)}>选择候选</button>}
                      {item.target.kind === "direct-image" && item.state === "succeeded" && <button type="button" onClick={() => downloadDirect(item.target.id)}>下载 GLB</button>}
                    </div>
                  ))}
                </div>
                {Boolean(batch.summary.uncertain || batch.summary.planned) && <button type="button" onClick={resumeBatch} disabled={busy}>使用原目标 ID 恢复</button>}
                <button type="button" className="primary" onClick={() => setBatch(undefined)}>新建批次</button>
              </div>
            ) : batchMode === "prompts" ? (
              <>
                <p>每行一个提示词；每项创建独立 Experiment，并在候选图完成后等待人工选择。</p>
                <textarea value={batchPrompts} onChange={(event) => setBatchPrompts(event.target.value)} placeholder="黄铜天文仪&#10;白色陶瓷机器人&#10;木制机械鸟" required />
                <div className="form-row">
                  <label>2D 模型<select value={imageModel} onChange={(event) => setImageModel(event.target.value)}>{(imageProvider?.models ?? []).map((model) => <option key={model.id}>{model.id}</option>)}</select></label>
                  <label>每项候选数<input type="number" min={1} max={8} value={count} onChange={(event) => setCount(Number(event.target.value))} /></label>
                  <button className="primary" disabled={busy || !promptItems.length}>提交 {promptItems.length || ""} 项 / {promptItems.length * count || ""} 个 2D Job</button>
                </div>
              </>
            ) : (
              <>
                <p>选择最多 50 张 PNG/JPEG/WebP；输入按内容寻址保存，每张图片创建独立 3D Run。</p>
                <input className="file-input" type="file" accept="image/png,image/jpeg,image/webp" multiple onChange={(event) => setBatchFiles(Array.from(event.target.files ?? []).slice(0, 50))} required />
                <div className="form-row">
                  <label>3D 模型<select value={assetModel} onChange={(event) => setAssetModel(event.target.value)}>{(assetProvider?.models ?? []).map((model) => <option key={model.id}>{model.id}</option>)}</select></label>
                  <label>Profile<select value={profile} onChange={(event) => setProfile(event.target.value)}>{(profiles.length ? profiles : [{ id: "recommended" }]).map((item) => <option key={item.id}>{item.id}</option>)}</select></label>
                  <button className="primary" disabled={busy || !batchFiles.length || !assetModel}>上传并提交 {batchFiles.length || ""} 张图片</button>
                </div>
              </>
            )}
          </form>
        ) : !current ? (
          <form className="new-experiment" onSubmit={create}>
            <span className="eyebrow">NEW EXPERIMENT</span>
            <h2>从一个可比较、可复现的实验开始</h2>
            <p>生成多张 2D 候选；你做语义选择；选中的原始 Artifact 直接交给 3D Sidecar。</p>
            <textarea
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              placeholder="例如：一个磨损的黄铜天文仪，纯色背景，完整物体，正交三分之四视角"
              required
            />
            <div className="form-row">
              <label>2D 模型<select value={imageModel} onChange={(event) => setImageModel(event.target.value)}>
                {(imageProvider?.models ?? []).map((model) => <option key={model.id}>{model.id}</option>)}
                {!imageProvider?.models.length && <option>{imageModel || "sana-sprint-1.6b"}</option>}
              </select></label>
              <label>候选数<input type="number" min={1} max={8} value={count} onChange={(event) => setCount(Number(event.target.value))} /></label>
              <button className="primary" disabled={busy || !prompt.trim()}>创建并运行</button>
            </div>
          </form>
        ) : (
          <div className="experiment">
            <header className="experiment-head">
              <div><span className="eyebrow">{current.id}</span><h2>{current.prompt}</h2></div>
              <div className="head-actions">
                {canResume && <button onClick={resume} disabled={busy}>使用原 Job ID 恢复</button>}
                <span className="phase">{current.phase}</span>
              </div>
            </header>
            <div className="flow">
              <span className={current.image.candidates.length ? "done" : ""}>01 生成候选</span>
              <b>→</b><span className={current.selection ? "done" : ""}>02 人工选择</span>
              <b>→</b><span className={current.asset3d ? "done" : ""}>03 生成 3D</span>
              <b>→</b><span className={current.phase === "complete" ? "done" : ""}>04 验证产物</span>
            </div>
            <section>
              <div className="section-title"><div><span className="eyebrow">CANDIDATES</span><h3>选择最符合意图的图像</h3></div><small>{current.image.model}</small></div>
              <div className="candidate-grid">
                {current.image.candidates.map((candidate) => (
                  <button
                    key={candidate.id}
                    className={current.selection?.candidateId === candidate.id ? "candidate selected" : "candidate"}
                    disabled={busy || candidate.job.state !== "succeeded"}
                    onClick={() => choose(candidate.id)}
                  >
                    <div className="image"><ArtifactImage api={api!} experiment={current.id} candidate={candidate} /></div>
                    <span><strong>候选 {candidate.ordinal}</strong><small>seed {candidate.seed} · {candidate.job.state}</small></span>
                  </button>
                ))}
              </div>
            </section>
            <section className="asset-panel">
              <div className="section-title"><div><span className="eyebrow">ASSET 3D</span><h3>从已选择 Artifact 派生</h3></div></div>
              {!current.selection ? <p>先选择一张成功候选图。</p> : !current.asset3d ? (
                <div className="form-row">
                  <label>3D 模型<select value={assetModel} onChange={(event) => setAssetModel(event.target.value)}>
                    {(assetProvider?.models ?? []).map((model) => <option key={model.id}>{model.id}</option>)}
                    {!assetProvider?.models.length && <option value="">等待 3D Sidecar</option>}
                  </select></label>
                  <label>Profile<select value={profile} onChange={(event) => setProfile(event.target.value)}>
                    {(profiles.length ? profiles : [{ id: "recommended" }]).map((item) => <option key={item.id}>{item.id}</option>)}
                  </select></label>
                  <button className="primary" disabled={busy || !assetModel} onClick={generate}>生成 3D</button>
                </div>
              ) : current.phase === "complete" && glbUrl ? (
                <div className="result">
                  <Suspense fallback={<div className="viewer" />}>
                    <GlbViewer url={glbUrl} />
                  </Suspense>
                  <a href={glbUrl} download={`${current.id}.glb`}>下载 GLB</a>
                </div>
              ) : <div className="running"><i /><span>{current.asset3d.job.state}</span><small>{current.asset3d.job.failure}</small></div>}
            </section>
          </div>
        )}
        {error && <div className="error" onClick={() => setError(undefined)}>{error}</div>}
      </section>
    </main>
  );
}
