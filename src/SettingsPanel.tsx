import { useState } from "react";
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
  if (!open) return null;

  const {
    agent, agentMessage, modalConnected, modalMessage,
    tokenId, setTokenId, tokenSecret, setTokenSecret,
    persistence, remember, setRemember, runtime, operations,
  } = controller;
  const active = (name: typeof operations[number]) => operations.includes(name);
  const busy = operations.length > 0;
  const preprocessing = runtime?.preprocessing;

  return (
    <div className="settings-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div className="settings-shell" role="dialog" aria-modal="true" aria-label="设置">
        <aside className="settings-nav">
          <div className="settings-brand"><strong>modal-3D</strong><small>Settings</small></div>
          <button type="button" className={page === "account" ? "active" : ""} onClick={() => setPage("account")}><span>◈</span><div>Modal 账户<small>{modalConnected ? "已连接" : "未连接"}</small></div></button>
          <button type="button" className={page === "preprocess" ? "active" : ""} onClick={() => setPage("preprocess")}><span>AI</span><div>本地预处理<small>{preprocessing?.engine ?? "rembg"}</small></div></button>
          <button type="button" className={page === "advanced" ? "active" : ""} onClick={() => setPage("advanced")}><span>⋯</span><div>高级与诊断<small>{agent?.running ? "Agent 正常" : "Agent 停止"}</small></div></button>
          <button type="button" className="settings-close" onClick={onClose}>关闭</button>
        </aside>

        <div className="settings-content">
          {page === "account" ? (
            <section className="settings-page">
              <div className="settings-page-title"><div><span className="eyebrow">Connection</span><h3>Modal 账户</h3><p>只用于发现 3D Worker、上传一次 Canonical RGBA 和提交生成任务。</p></div><Status ok={modalConnected}>{modalConnected ? "已连接" : "未连接"}</Status></div>
              <div className="settings-form">
                <label><span>Token ID</span><input value={tokenId} onChange={(event) => setTokenId(event.target.value)} placeholder="ak-…" autoComplete="off" /></label>
                <label><span>Token Secret</span><input type="password" value={tokenSecret} onChange={(event) => setTokenSecret(event.target.value)} placeholder="as-…" autoComplete="off" /></label>
                {persistence.supported ? <label className="remember-row"><input type="checkbox" checked={remember} onChange={(event) => setRemember(event.target.checked)} /><span>保存到 Windows 凭据管理器</span></label> : null}
              </div>
              <div className="settings-actions">
                {modalConnected ? <button type="button" className="quiet-button" disabled={busy} onClick={() => void controller.disconnect()}>{active("disconnect") ? "断开中…" : "断开"}</button> : <button type="button" className="primary-button" disabled={busy || !tokenId.trim() || !tokenSecret.trim()} onClick={() => void controller.connect()}>{active("connect") ? "连接中…" : "连接 Modal"}</button>}
                {persistence.stored ? <button type="button" className="quiet-button danger-text" disabled={busy} onClick={() => void controller.forget()}>删除保存凭据</button> : null}
              </div>
              <div className="settings-explainer"><strong>数据边界</strong><p>原图和 rembg 抠图都只在本机处理。只有最终 1024×1024 Canonical RGBA 会在点击生成时上传一次。</p></div>
              <p className="control-explanation">{modalMessage}</p>
            </section>
          ) : null}

          {page === "preprocess" ? (
            <section className="settings-page">
              <div className="settings-page-title"><div><span className="eyebrow">Local preprocessing</span><h3>rembg 全局显著性抠图</h3><p>当前测试阶段固定使用 birefnet-general + CPU；不调用云端预处理。</p></div><Status ok={Boolean(preprocessing)}>{preprocessing ? "本地" : "待检测"}</Status></div>
              <div className="diagnostic-grid">
                <div><span>引擎</span><strong>{preprocessing?.engine ?? "birefnet-general"}</strong></div>
                <div><span>执行设备</span><strong>{preprocessing?.provider?.toUpperCase() ?? "CPU"}</strong></div>
                <div><span>Canonical</span><strong>{preprocessing ? `${preprocessing.canonical_size}×${preprocessing.canonical_size}` : "1024×1024"}</strong></div>
                <div><span>模型状态</span><strong>{preprocessing?.model_downloaded ? "已缓存" : "首次抠图自动下载"}</strong></div>
                <div className="wide"><span>模型目录</span><code>{preprocessing?.model_home ?? "启动 Agent 后显示"}</code></div>
              </div>
              <div className="settings-explainer"><strong>Canonical 规则</strong><p>rembg 只生成全局 Alpha；随后按前景联合包围盒严格保持高宽比，等比缩放并透明 Letterbox 到 1024×1024。</p></div>
              <div className="settings-actions"><button type="button" className="quiet-button" disabled={!agent?.running || busy} onClick={() => void controller.refresh()}>{active("refresh") ? "刷新中…" : "刷新状态"}</button></div>
            </section>
          ) : null}

          {page === "advanced" ? (
            <section className="settings-page">
              <div className="settings-page-title"><div><span className="eyebrow">Diagnostics</span><h3>高级与诊断</h3><p>{agentMessage}</p></div><Status ok={Boolean(agent?.running)}>{agent?.running ? "运行中" : "已停止"}</Status></div>
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
            </section>
          ) : null}
        </div>
      </div>
    </div>
  );
}
