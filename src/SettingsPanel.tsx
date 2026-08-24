import { useEffect, useRef, useState, type ReactNode } from "react";
import type { SamMode } from "./agent";
import type { RuntimeAction, RuntimeController } from "./useRuntimeController";

type SettingsPage = "account" | "sam" | "advanced";

type SettingsPanelProps = {
  open: boolean;
  onClose: () => void;
  controller: RuntimeController;
};

const SAM_OPTIONS: { mode: SamMode; name: string; description: string }[] = [
  { mode: "auto", name: "自动（推荐）", description: "本机可用时优先本机，失败后自动使用云端。" },
  { mode: "cloud", name: "仅云端", description: "始终使用 Modal SAM，省去本机安装和显存占用。" },
  { mode: "local", name: "仅本机", description: "数据留在本机；运行时不可用时会明确停止任务。" },
];

function formatBytes(bytes: number | null | undefined) {
  if (!bytes) return "0 B";
  const units = ["B", "KiB", "MiB", "GiB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(unit > 1 ? 1 : 0)} ${units[unit]}`;
}

function Status({ ok, children }: { ok: boolean; children: ReactNode }) {
  return <span className={`settings-state ${ok ? "ok" : ""}`}><i aria-hidden="true" />{children}</span>;
}

export default function SettingsPanel({ open, onClose, controller }: SettingsPanelProps) {
  const [page, setPage] = useState<SettingsPage>("account");
  const dialogRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialogRef.current?.focus();
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", close);
    return () => {
      window.removeEventListener("keydown", close);
      previous?.focus();
    };
  }, [onClose, open]);

  if (!open) return null;

  const {
    agent, agentMessage, modalConnected, modalMessage, tokenId, setTokenId,
    tokenSecret, setTokenSecret, persistence, remember, setRemember, runtime,
    models, operations, notice,
  } = controller;
  const local = runtime?.sam.local;
  const agentReady = Boolean(agent?.running);
  const active = (action: RuntimeAction) => operations.includes(action);
  const agentBusy = active("agent") || active("refresh");
  const connectionBusy = agentBusy || active("connect") || active("disconnect") || active("forget");
  const samBusy = agentBusy || active("mode") || active("install") || active("verify") || active("migrate") || active("uninstall");
  const credentialsReady = Boolean(tokenId.trim() && tokenSecret.trim());
  const connectReason = !agentReady
    ? "先启动本地服务"
    : !credentialsReady
      ? "填写令牌 ID 和令牌密钥后即可连接"
      : "";
  const installReason = !modalConnected
    ? "安装需要连接 Modal，用于安全下载 SAM 3.1 模型文件。"
    : !local?.supported_platform
      ? "Local SAM 当前仅支持 Windows x86_64。"
      : !local.hardware_eligible
        ? local.reason
        : !local.disk_eligible
          ? `磁盘空间不足，至少需要 ${(local.min_disk_mib / 1024).toFixed(1)} GiB。`
          : "";
  const installVisible = Boolean(local && (!local.installed || local.update_available));

  return (
    <div className="settings-backdrop" role="presentation" onMouseDown={onClose}>
      <section ref={dialogRef} tabIndex={-1} className="settings-dialog settings-v2" role="dialog" aria-modal="true" aria-labelledby="settings-title" onMouseDown={(event) => event.stopPropagation()}>
        <header className="settings-header">
          <div><span className="eyebrow">modal-3D Studio</span><h2 id="settings-title">设置</h2></div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="关闭设置">×</button>
        </header>

        <div className="settings-layout">
          <nav className="settings-nav" aria-label="设置分类">
            <button type="button" className={page === "account" ? "active" : ""} onClick={() => setPage("account")}><span>M</span><div>Modal 账户<small>{modalConnected ? "已连接" : "需要连接"}</small></div></button>
            <button type="button" className={page === "sam" ? "active" : ""} onClick={() => setPage("sam")}><span>AI</span><div>SAM 推理<small>{runtime?.sam.effective ?? "待检测"}</small></div></button>
            <button type="button" className={page === "advanced" ? "active" : ""} onClick={() => setPage("advanced")}><span>···</span><div>高级与诊断<small>{agentReady ? "服务正常" : "服务离线"}</small></div></button>
            <div className="settings-nav-health"><Status ok={agentReady}>{agentReady ? "本地服务正常" : "本地服务离线"}</Status><Status ok={modalConnected}>{modalConnected ? "Modal 在线" : "Modal 未连接"}</Status></div>
          </nav>

          <div className="settings-content">
            {notice ? <div className={`settings-notice ${notice.tone}`} role="status"><span>{notice.text}</span><button type="button" onClick={controller.dismissNotice} aria-label="关闭提示">×</button></div> : null}

            {!agentReady && page !== "advanced" ? <div className="settings-recovery"><div><strong>本地服务没有运行</strong><p>账户验证、能力检测和运行时管理都依赖本地服务。</p></div><button type="button" className="primary-button" disabled={!controller.inTauri || agentBusy} onClick={() => void controller.start()}>{active("agent") ? "启动中…" : "启动服务"}</button></div> : null}

            {page === "account" ? (
              <section className="settings-page" aria-labelledby="account-title">
                <div className="settings-page-title"><div><span className="eyebrow">Connection</span><h3 id="account-title">Modal 账户</h3><p>用于发现云端模型、运行 Cloud SAM 和提交 3D 任务。</p></div><Status ok={modalConnected}>{modalConnected ? "已连接" : "未连接"}</Status></div>
                {modalConnected ? (
                  <div className="account-connected-card">
                    <div className="connection-identity"><span className="connection-avatar">M</span><div><strong>Modal Workspace</strong><p>{modalMessage}</p><small>已发现 {models.length} 个可用 3D 模型</small></div></div>
                    <div className="settings-actions">{persistence.stored ? <button type="button" className="quiet-button danger-text" disabled={connectionBusy} onClick={() => void controller.forget()}>删除凭据</button> : null}<button type="button" className="quiet-button" disabled={connectionBusy} onClick={() => void controller.disconnect()}>{active("disconnect") ? "断开中…" : "断开连接"}</button></div>
                  </div>
                ) : (
                  <form className="account-form" onSubmit={(event) => { event.preventDefault(); if (!connectReason && !connectionBusy) void controller.connect(); }}>
                    <div className="settings-field"><label htmlFor="modal-token-id">令牌 ID</label><input id="modal-token-id" value={tokenId} onChange={(event) => setTokenId(event.target.value)} autoComplete="off" spellCheck={false} placeholder="ak-…" /></div>
                    <div className="settings-field"><label htmlFor="modal-token-secret">令牌密钥</label><input id="modal-token-secret" type="password" value={tokenSecret} onChange={(event) => setTokenSecret(event.target.value)} autoComplete="off" placeholder="••••••••••••" /></div>
                    <label className="remember-row"><input type="checkbox" checked={remember} disabled={!persistence.supported} onChange={(event) => setRemember(event.target.checked)} /><span><strong>在这台电脑上记住</strong><small>{persistence.supported ? "凭据加密保存在 Windows 凭据管理器，不会回传到界面。" : "当前环境不支持安全保存。"}</small></span></label>
                    <div className="form-submit-row"><span className={connectReason ? "warning" : ""}>{connectReason || modalMessage}</span><button className="primary-button" disabled={Boolean(connectReason) || connectionBusy}>{active("connect") ? "正在验证…" : "连接 Modal"}</button></div>
                  </form>
                )}
                <div className="settings-explainer"><strong>为什么需要 Modal？</strong><p>客户端只把凭据交给本机 Agent。Agent 通过新版 <code>modal-3D.capabilities.v1</code> 动态发现模型，并以可恢复的异步任务提交生成；React 不保存密钥，也不硬编码云端模型。</p></div>
              </section>
            ) : null}

            {page === "sam" ? (
              <section className="settings-page" aria-labelledby="sam-title">
                <div className="settings-page-title"><div><span className="eyebrow">Segmentation</span><h3 id="sam-title">SAM 推理</h3><p>选择对象识别发生在本机还是 Modal 云端。</p></div><button type="button" className="text-button" disabled={!agentReady || agentBusy || samBusy} onClick={() => void controller.refresh()}>{active("refresh") ? "刷新中…" : "刷新状态"}</button></div>
                <div className="provider-options" role="radiogroup" aria-label="SAM 推理模式">
                  {SAM_OPTIONS.map((option) => {
                    const selected = runtime?.sam.mode === option.mode;
                    const available = option.mode === "auto" ? Boolean(runtime?.sam.effective) : option.mode === "cloud" ? Boolean(runtime?.sam.cloud.available) : Boolean(local?.available);
                    return <button key={option.mode} type="button" role="radio" aria-checked={selected} className={selected ? "selected" : ""} disabled={!agentReady || samBusy} onClick={() => void controller.changeSamMode(option.mode)}><span className="provider-radio" aria-hidden="true" /><div><strong>{option.name}</strong><p>{option.description}</p></div><small className={available ? "available" : ""}>{available ? "可用" : "当前不可用"}</small></button>;
                  })}
                </div>

                {local ? (
                  <div className="local-runtime-panel">
                    <div className="runtime-card-head"><div><span className="eyebrow">Optional runtime</span><strong>Local SAM 3.1</strong><p>{local.reason}</p></div><Status ok={local.ready}>{local.ready ? "运行中" : local.installing ? "安装中" : local.installed ? "已安装" : "未安装"}</Status></div>
                    <div className="runtime-facts"><div><span>GPU</span><strong>{local.gpu?.name ?? "未检测到 NVIDIA GPU"}</strong><small>{local.gpu ? `${(local.gpu.memory_mib / 1024).toFixed(1)} GiB VRAM` : `最低 ${(local.min_vram_mib / 1024).toFixed(1)} GiB`}</small></div><div><span>可用磁盘</span><strong>{(runtime.hardware.disk_free_mib / 1024).toFixed(1)} GiB</strong><small>安装至少需要 {(local.min_disk_mib / 1024).toFixed(1)} GiB</small></div></div>
                    <div className="runtime-location"><div><span>存储目录</span><code title={local.root_path}>{local.root_path}</code></div><button type="button" className="quiet-button" disabled={!agentReady || samBusy || local.installing} onClick={() => void controller.migrate()}>{active("migrate") ? "迁移中…" : local.installed ? "迁移" : "更改"}</button></div>
                    {local.installing ? <div className="download-progress"><div><span>{controller.localProgress}</span><strong>{local.download_speed_bps ? `${(local.download_speed_bps / 1024 / 1024).toFixed(1)} MiB/s` : "处理中"}</strong></div><progress value={local.downloaded_bytes ?? 0} max={local.download_total_bytes ?? local.checkpoint_bytes} /><small>{formatBytes(local.downloaded_bytes)} / {formatBytes(local.download_total_bytes ?? local.checkpoint_bytes)} · 安装在后台继续，可以关闭设置</small></div> : null}
                    <div className="runtime-actions-row"><div>{installVisible && installReason ? <p className="control-explanation warning">{installReason}</p> : <p className="control-explanation">模型文件约 3.3 GiB，Torch/CUDA 运行环境会额外占用空间。</p>}</div><div className="settings-actions">{local.installed && !local.ready ? <button type="button" className="quiet-button" disabled={samBusy || local.installing} onClick={() => void controller.verify()}>{active("verify") ? "验证中…" : "启动并验证"}</button> : null}{local.installed || local.update_available ? <button type="button" className="quiet-button danger-text" disabled={samBusy || local.installing} onClick={() => void controller.uninstall()}>{active("uninstall") ? "卸载中…" : "卸载"}</button> : null}{installVisible ? <button type="button" className="primary-button" disabled={Boolean(installReason) || samBusy || local.installing} onClick={() => void controller.install()}>{local.installing ? "安装中…" : active("install") ? "正在开始…" : local.update_available ? "更新运行时" : "安装运行时"}</button> : null}</div></div>
                  </div>
                ) : <div className="settings-recovery"><div><strong>尚未取得运行时状态</strong><p>点击刷新重试；如果仍失败，请到“高级与诊断”重启本地服务。</p></div><button type="button" className="quiet-button" disabled={!agentReady || agentBusy} onClick={() => void controller.refresh()}>重新检测</button></div>}
              </section>
            ) : null}

            {page === "advanced" ? (
              <section className="settings-page" aria-labelledby="advanced-title">
                <div className="settings-page-title"><div><span className="eyebrow">Diagnostics</span><h3 id="advanced-title">高级与诊断</h3><p>普通使用无需调整；遇到连接或运行时问题时从这里恢复。</p></div></div>
                <div className="advanced-service-card"><div><strong>本地 Agent</strong><p>{controller.inTauri ? agentMessage : "浏览器预览不包含桌面服务，请运行 Windows 客户端。"}</p></div><button type="button" className={agentReady ? "quiet-button danger-text" : "primary-button"} disabled={!controller.inTauri || agentBusy || Boolean(local?.installing)} onClick={() => void (agentReady ? controller.stop() : controller.start())}>{active("agent") ? "处理中…" : agentReady ? "停止服务" : "启动服务"}</button></div>
                <div className="diagnostic-grid"><div><span>客户端版本</span><strong>{controller.diagnostics?.version ?? "开发预览"}</strong></div><div><span>本地端口</span><strong>{agent?.port ? `127.0.0.1:${agent.port}` : "未监听"}</strong></div><div><span>平台</span><strong>{runtime ? `${runtime.hardware.platform} · ${runtime.hardware.machine}` : "待检测"}</strong></div><div><span>内存</span><strong>{runtime?.hardware.memory_mib ? `${(runtime.hardware.memory_mib / 1024).toFixed(1)} GiB` : "未知"}</strong></div><div className="wide"><span>显卡</span><strong>{runtime?.hardware.gpus[0]?.name ?? "未检测"}</strong></div><div className="wide"><span>应用数据目录</span><code>{controller.diagnostics?.data_dir ?? "桌面客户端启动后显示"}</code></div><div className="wide"><span>Agent 日志</span><code>{controller.diagnostics?.agent_log ?? "当前没有 Agent 日志"}</code></div><div className="wide"><span>Local SAM 数据目录</span><code>{local?.root_path ?? "启动服务后显示"}</code></div></div>
                <div className="advanced-actions"><button type="button" className="quiet-button" disabled={!controller.inTauri} onClick={() => void controller.openDataDirectory()}>打开数据目录</button><button type="button" className="quiet-button" disabled={!agentReady || agentBusy || samBusy || connectionBusy} onClick={() => void controller.refresh()}>{active("refresh") ? "检查中…" : "重新检查全部状态"}</button></div>
                <div className="settings-explainer"><strong>恢复顺序</strong><p>先重新检查状态；无响应时重启本地 Agent；只有 Local SAM 文件损坏时才卸载并重装运行时。停止 Agent 不会删除项目、凭据或模型文件。</p></div>
              </section>
            ) : null}
          </div>
        </div>
      </section>
    </div>
  );
}
