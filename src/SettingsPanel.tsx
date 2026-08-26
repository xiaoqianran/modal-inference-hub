import { useEffect, useRef, useState } from "react";
import { parseModalCommand } from "./modalCommand";
import type { RuntimeController } from "./useRuntimeController";

type SettingsPage = "account" | "preprocess" | "advanced";

function Status({ ok, children }: { ok: boolean; children: string }) {
  return <span className={`status-chip ${ok ? "ok" : ""}`}>{children}</span>;
}

export default function SettingsPanel({
  open,
  onClose,
  controller,
}: {
  open: boolean;
  onClose: () => void;
  controller: RuntimeController;
}) {
  const [page, setPage] = useState<SettingsPage>("account");
  const dialogRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    if (!open) return;
    const previousFocus = document.activeElement as HTMLElement | null;
    const dialog = dialogRef.current;
    const focusableSelector = "button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex='-1'])";
    const frame = window.requestAnimationFrame(() => {
      (dialog?.querySelector<HTMLElement>(focusableSelector) ?? dialog)?.focus();
    });

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab" || !dialog) return;
      const focusable = [...dialog.querySelectorAll<HTMLElement>(focusableSelector)];
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("keydown", handleKeyDown);
      previousFocus?.focus();
    };
  }, [open]);

  if (!open) return null;

  const {
    agent, agentMessage, modalConnected, modalMessage,
    tokenId, setTokenId, tokenSecret, setTokenSecret,
    commandText, applyCommand,
    persistence, remember, setRemember, runtime, operations,
    rembgDeployed,
  } = controller;
  const commandParsed = commandText.trim() ? parseModalCommand(commandText) !== null : false;
  const active = (name: typeof operations[number]) => operations.includes(name);
  const busy = operations.length > 0;
  const preprocessing = runtime?.preprocessing;

  return (
    <div className="settings-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div ref={dialogRef} className="settings-shell" role="dialog" aria-modal="true" aria-labelledby="settings-title" tabIndex={-1}>
        <aside className="settings-nav">
          <div className="settings-brand"><strong id="settings-title">设置</strong><small>连接与系统</small></div>
          <button type="button" className={page === "account" ? "active" : ""} aria-current={page === "account" ? "page" : undefined} onClick={() => setPage("account")}><span>云</span><div>云端连接<small>{modalConnected ? "Modal 已连接" : "等待凭据"}</small></div></button>
          <button type="button" className={page === "preprocess" ? "active" : ""} aria-current={page === "preprocess" ? "page" : undefined} onClick={() => setPage("preprocess")}><span>AI</span><div>本地模型<small>{preprocessing?.provider?.toUpperCase() ?? preprocessing?.engine ?? "rembg"}</small></div></button>
          <button type="button" className={page === "advanced" ? "active" : ""} aria-current={page === "advanced" ? "page" : undefined} onClick={() => setPage("advanced")}><span>诊</span><div>系统诊断<small>{agent?.running ? "本地服务正常" : "本地服务停止"}</small></div></button>
          <button type="button" className="settings-close" onClick={onClose}>关闭</button>
        </aside>

        <div className="settings-content">
          {controller.notice ? (
            <div className={`settings-notice ${controller.notice.tone}`} role="status">
              <span>{controller.notice.text}</span>
              <button type="button" onClick={controller.dismissNotice} aria-label="关闭提示">×</button>
            </div>
          ) : null}
          <div className="command-status-overview" aria-label="系统状态">
            <div className={`command-status-card ${agent?.running ? "ok" : "warn"}`}>
              <i />
              <span><small>本地服务</small><strong>{agent?.running ? `127.0.0.1:${agent.port}` : "未启动"}</strong></span>
            </div>
            <div className={`command-status-card ${preprocessing ? "ok" : "warn"}`}>
              <i />
              <span><small>本地模型</small><strong>{preprocessing ? `${preprocessing.provider.toUpperCase()} · ${preprocessing.gpu_warm ? "已预热" : "就绪"}` : "待检测"}</strong></span>
            </div>
            <div className={`command-status-card ${modalConnected ? "ok" : "warn"}`}>
              <i />
              <span><small>Modal 云</small><strong>{modalConnected ? "已连接" : "未连接"}</strong></span>
            </div>
          </div>
          {page === "account" ? (
            <section className="settings-page">
              <div className="settings-page-title"><div><span className="eyebrow">云端连接</span><h3>Modal 账户</h3><p>只用于发现 3D Worker、上传一次标准化前景并提交生成任务。</p></div><Status ok={modalConnected}>{modalConnected ? "已连接" : "未连接"}</Status></div>
              <div className="settings-form">
                <label className="command-paste-row">
                  <span>粘贴命令快速填入</span>
                  <input
                    value={commandText}
                    onChange={(event) => applyCommand(event.target.value)}
                    placeholder="modal token set --token-id ak-… --token-secret as-…"
                    autoComplete="off"
                    spellCheck={false}
                  />
                </label>
                {commandText.trim() ? (
                  <p className={`command-paste-hint ${commandParsed ? "ok" : ""}`}>
                    {commandParsed ? "已识别 Token ID 与密钥，自动填入下方" : "未识别凭据；请粘贴完整的 modal token set 命令"}
                  </p>
                ) : null}
                <label><span>Token ID</span><input value={tokenId} onChange={(event) => setTokenId(event.target.value)} placeholder="ak-…" autoComplete="off" /></label>
                <label><span>Token Secret</span><input type="password" value={tokenSecret} onChange={(event) => setTokenSecret(event.target.value)} placeholder="as-…" autoComplete="off" /></label>
                {persistence.supported ? <label className="remember-row"><input type="checkbox" checked={remember} onChange={(event) => setRemember(event.target.checked)} /><span>保存到 Windows 凭据管理器</span></label> : null}
              </div>
              <div className="settings-actions">
                {modalConnected ? <button type="button" className="quiet-button" disabled={busy} onClick={() => void controller.disconnect()}>{active("disconnect") ? "断开中…" : "断开"}</button> : <button type="button" className="primary-button" disabled={busy || !tokenId.trim() || !tokenSecret.trim()} onClick={() => void controller.connect()}>{active("connect") ? "连接中…" : "连接 Modal"}</button>}
                {persistence.stored ? <button type="button" className="quiet-button danger-text" disabled={busy} onClick={() => void controller.forget()}>删除保存凭据</button> : null}
              </div>
              {modalConnected ? (
                <div className="deploy-card">
                  <div className="deploy-card-head">
                    <div>
                      <strong>云端抠图应用</strong>
                      <p>没有 NVIDIA GPU 时，抠图会在你自己 Modal 账户的 T4 上运行。首次使用需先部署这个应用（约几分钟构建一次镜像）。</p>
                    </div>
                    <span className={`status-chip ${rembgDeployed ? "ok" : ""}`}>{rembgDeployed === null ? "检测中" : rembgDeployed ? "已部署" : "未部署"}</span>
                  </div>
                  <div className="settings-actions">
                    <button
                      type="button"
                      className="primary-button"
                      disabled={busy}
                      onClick={() => void controller.deployRembg()}
                    >
                      {active("deploy") ? "部署中…" : rembgDeployed ? "重新部署 / 更新" : "一键部署云端抠图"}
                    </button>
                  </div>
                </div>
              ) : null}
              <div className="settings-explainer"><strong>数据边界</strong><p>原图和 rembg 抠图都只在本机处理。只有最终 1024×1024 Canonical RGBA 会在点击生成时上传一次。</p></div>
              <p className="control-explanation">{modalMessage}</p>
            </section>
          ) : null}

          {page === "preprocess" ? (
            <section className="settings-page">
              <div className="settings-page-title"><div><span className="eyebrow">抠图预处理</span><h3>rembg 全局显著性抠图</h3><p>默认自动：检测到 NVIDIA GPU 就在本机离线抠图，否则使用云端。也可以手动固定为云端或本地。</p></div><Status ok={Boolean(preprocessing)}>{preprocessing?.resolved_execution === "local" ? "本地" : preprocessing?.resolved_execution === "cloud" ? "云端" : "待检测"}</Status></div>

              <div className="provider-options" role-label="执行位置">
                <button
                  type="button"
                  className={preprocessing?.execution !== "cloud" && preprocessing?.execution !== "local" ? "selected" : ""}
                  disabled={!agent?.running || busy}
                  onClick={() => void controller.changePreprocessExecution("auto")}
                >
                  <span className="provider-radio" />
                  <div><strong>自动（推荐）</strong><p>检测到 NVIDIA GPU 就在本机抠图；没有则用云端，无需 GPU 或下载模型。</p></div>
                  <small className="available">{preprocessing?.resolved_execution === "local" ? "当前：本地" : preprocessing?.resolved_execution === "cloud" ? "当前：云端" : "检测中"}</small>
                </button>
                <button
                  type="button"
                  className={preprocessing?.execution === "cloud" ? "selected" : ""}
                  disabled={!agent?.running || busy}
                  onClick={() => void controller.changePreprocessExecution("cloud")}
                >
                  <span className="provider-radio" />
                  <div><strong>云端抠图</strong><p>固定使用 Modal T4，本机无需 GPU、无需下载模型；只上传原图用于抠图。</p></div>
                  <small className={preprocessing?.cloud_connected ? "available" : ""}>{preprocessing?.cloud_connected ? "已连接" : "未连接"}</small>
                </button>
                <button
                  type="button"
                  className={preprocessing?.execution === "local" ? "selected" : ""}
                  disabled={!agent?.running || busy}
                  onClick={() => void controller.changePreprocessExecution("local")}
                >
                  <span className="provider-radio" />
                  <div><strong>本地抠图</strong><p>固定完全离线，在本机运行 rembg；首次使用需下载模型，可选 CPU 或 GPU。</p></div>
                  <small className={preprocessing?.gpu_available ? "available" : ""}>{preprocessing?.gpu_available ? "检测到 GPU" : "未检测到 GPU"}</small>
                </button>
              </div>

              {preprocessing?.resolved_execution === "local" ? (
              <>
              <div className="section-label" style={{ marginTop: 4 }}><span>本地执行后端</span></div>
              <div className="provider-options">
                <button
                  type="button"
                  className={preprocessing?.provider_preference === "cpu" ? "selected" : ""}
                  disabled={!agent?.running || busy}
                  onClick={() => void controller.changePreprocessProvider("cpu")}
                >
                  <span className="provider-radio" />
                  <div><strong>CPU</strong><p>兼容性最高；使用最多 {preprocessing?.cpu_threads ?? 8} 个本地线程。</p></div>
                  <small className="available">可用</small>
                </button>
                <button
                  type="button"
                  className={preprocessing?.provider_preference === "gpu" ? "selected" : ""}
                  disabled={!agent?.running || busy || !preprocessing?.gpu_available}
                  onClick={() => void controller.changePreprocessProvider("gpu")}
                >
                  <span className="provider-radio" />
                  <div><strong>GPU</strong><p>Windows 使用 ONNXRuntime CUDA（cuDNN）；选中后常驻预热，首次启动会后台准备模型。</p></div>
                  <small className={preprocessing?.gpu_available ? "available" : ""}>{preprocessing?.gpu_warm ? "已预热" : preprocessing?.gpu_available ? "可用" : "未安装"}</small>
                </button>
              </div>
              </>
              ) : null}
              {preprocessing?.resolved_execution === "local" ? (
              <div className="diagnostic-grid">
                <div><span>引擎</span><strong>{preprocessing?.engine ?? "birefnet-general-lite"}</strong></div>
                <div><span>实际执行</span><strong>{preprocessing?.provider?.toUpperCase() ?? "CPU"}</strong></div>
                <div><span>Canonical</span><strong>{preprocessing ? `${preprocessing.canonical_size}×${preprocessing.canonical_size}` : "1024×1024"}</strong></div>
                <div><span>模型状态</span><strong>{preprocessing?.model_downloaded ? (preprocessing.download?.integrity === "verified" ? "已缓存 · 已校验" : "已缓存") : preprocessing?.download?.resumable ? "可断点续传" : preprocessing?.provider_preference === "gpu" ? "启动后自动准备" : "首次抠图自动下载"}</strong></div>
                <div className="wide"><span>ONNXRuntime Providers</span><code>{preprocessing?.ort_providers?.join(" · ") || "启动 Agent 后检测"}</code></div>
                <div className="wide"><span>模型目录</span><code>{preprocessing?.model_home ?? "启动 Agent 后显示"}</code></div>
              </div>
              ) : (
              <div className="diagnostic-grid">
                <div><span>执行位置</span><strong>云端 · Modal T4</strong></div>
                <div><span>连接状态</span><strong>{preprocessing?.cloud_connected ? "已连接" : "未连接"}</strong></div>
                <div><span>引擎</span><strong>{preprocessing?.engine ?? "birefnet-general-lite"}</strong></div>
                <div><span>标准图</span><strong>{preprocessing ? `${preprocessing.canonical_size}×${preprocessing.canonical_size}` : "1024×1024"}</strong></div>
                <div className="wide"><span>说明</span><code>云端抠图只上传原图；标准化与组件分析仍在本机完成</code></div>
              </div>
              )}
              {preprocessing?.resolved_execution === "local" && preprocessing?.download && preprocessing.download.status !== "ready" ? (
                <div className="runtime-card">
                  <div className="runtime-card-head">
                    <div>
                      <strong>birefnet-general-lite 模型准备</strong>
                      <span>
                        {preprocessing.download.status === "downloading"
                          ? "正在下载"
                          : preprocessing.download.status === "verifying"
                            ? "正在校验完整性"
                            : preprocessing.download.status === "failed"
                              ? preprocessing.download.resumable ? "下载中断 · 可续传" : "准备失败"
                              : "尚未下载"}
                      </span>
                    </div>
                    <strong>{Math.floor(preprocessing.download.progress * 100)}%</strong>
                  </div>
                  <div className="download-progress">
                    <progress max={1} value={preprocessing.download.progress} />
                    <div>
                      <span>{(preprocessing.download.downloaded_bytes / 1024 / 1024).toFixed(1)} MiB</span>
                      <span>{(preprocessing.download.total_bytes / 1024 / 1024).toFixed(1)} MiB</span>
                    </div>
                    {preprocessing.download.error ? <small className="download-error">{preprocessing.download.error}</small> : null}
                  </div>
                </div>
              ) : null}
              {preprocessing?.fallback_reason ? <div className="settings-explainer"><strong>Provider 回退</strong><p>{preprocessing.fallback_reason}</p></div> : null}
              <div className="settings-explainer"><strong>标准图规则</strong><p>rembg 只生成全局 Alpha；随后按前景联合包围盒严格保持高宽比，等比缩放并透明 Letterbox 到 1024×1024，作为 3D 生成的唯一上传内容。</p></div>
              <div className="settings-actions">
                {preprocessing?.resolved_execution === "local" && (!preprocessing?.model_downloaded || preprocessing.download.integrity !== "verified") ? (
                  <button type="button" className="primary-button" disabled={!agent?.running || busy} onClick={() => void controller.prepareModel()}>
                    {active("model")
                      ? preprocessing?.download.status === "verifying" ? "校验中…" : "准备中…"
                      : preprocessing?.download.resumable ? "续传模型" : "提前准备模型"}
                  </button>
                ) : null}
                <button type="button" className="quiet-button" disabled={!agent?.running || busy} onClick={() => void controller.refresh()}>{active("refresh") ? "刷新中…" : "刷新状态"}</button>
              </div>
            </section>
          ) : null}

          {page === "advanced" ? (
            <section className="settings-page">
              <div className="settings-page-title"><div><span className="eyebrow">系统诊断</span><h3>高级与诊断</h3><p>{agentMessage}</p></div><Status ok={Boolean(agent?.running)}>{agent?.running ? "运行中" : "已停止"}</Status></div>
              <div className="diagnostic-grid">
                <div><span>客户端版本</span><strong>{controller.diagnostics?.version ?? "开发预览"}</strong></div>
                <div><span>本地端口</span><strong>{agent?.port ? `127.0.0.1:${agent.port}` : "未监听"}</strong></div>
                <div><span>平台</span><strong>{runtime ? `${runtime.hardware.platform} · ${runtime.hardware.machine}` : "待检测"}</strong></div>
                <div><span>内存</span><strong>{runtime?.hardware.memory_mib ? `${(runtime.hardware.memory_mib / 1024).toFixed(1)} GiB` : "未知"}</strong></div>
                <div className="wide"><span>应用数据目录</span><code>{controller.diagnostics?.data_dir ?? "启动后显示"}</code></div>
                <div className="wide"><span>Agent 日志</span><code>{controller.diagnostics?.agent_log ?? "当前没有日志"}</code></div>
              </div>
              <div className="advanced-actions">
                <button type="button" className="quiet-button" disabled={!controller.inTauri} onClick={() => void controller.openDataDirectory()}>打开数据目录</button>
                <button type="button" className="quiet-button" disabled={!agent?.running || busy} onClick={() => void controller.refresh()}>重新检查状态</button>
                {agent?.running ? <button type="button" className="quiet-button danger-text" disabled={busy} onClick={() => void controller.stop()}>停止 Agent</button> : <button type="button" className="primary-button" disabled={busy} onClick={() => void controller.start()}>启动 Agent</button>}
              </div>
              <div className="settings-shortcuts" aria-label="工作流快捷键">
                <div><span>跳到图像与前景</span><kbd>Alt 1</kbd></div>
                <div><span>跳到 3D 重构</span><kbd>Alt 2</kbd></div>
                <div><span>开始生成</span><kbd>Ctrl Enter</kbd></div>
                <div><span>打开设置</span><kbd>Ctrl ,</kbd></div>
                <div><span>前景撤销 / 重做</span><kbd>Ctrl Z · Ctrl Shift Z</kbd></div>
              </div>
            </section>
          ) : null}
        </div>
      </div>
    </div>
  );
}
