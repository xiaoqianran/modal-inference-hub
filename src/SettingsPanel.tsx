import { useEffect, type Dispatch, type SetStateAction } from "react";
import type {
  AgentInfo,
  CredentialStatus,
  RuntimeCapabilities,
  SamMode,
} from "./agent";

const SAM_MODES: SamMode[] = ["auto", "cloud", "local"];

type SettingsPanelProps = {
  open: boolean;
  onClose: () => void;
  agent: AgentInfo | null;
  agentMessage: string;
  inTauri: boolean;
  onToggleAgent: () => void;
  modalConnected: boolean;
  modalMessage: string;
  tokenId: string;
  setTokenId: Dispatch<SetStateAction<string>>;
  tokenSecret: string;
  setTokenSecret: Dispatch<SetStateAction<string>>;
  persistence: CredentialStatus;
  remember: boolean;
  setRemember: Dispatch<SetStateAction<boolean>>;
  onConnect: () => void;
  onDisconnect: () => void;
  onForget: () => void;
  runtime: RuntimeCapabilities | null;
  localBusy: boolean;
  localProgress: string;
  onRefresh: () => void;
  onSamMode: (mode: SamMode) => void;
  onInstall: () => void;
  onVerify: () => void;
  onUninstall: () => void;
  onMigrate: () => void;
};

function StatusDot({ active }: { active: boolean }) {
  return <span className={`settings-status-dot ${active ? "active" : ""}`} aria-hidden="true" />;
}

export default function SettingsPanel(props: SettingsPanelProps) {
  useEffect(() => {
    if (!props.open) return;
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") props.onClose();
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [props.open, props.onClose]);

  if (!props.open) return null;
  const local = props.runtime?.sam.local;
  const agentReady = Boolean(props.agent?.running);
  const credentialsReady = Boolean(props.tokenId.trim() && props.tokenSecret.trim());
  const connectDisabledReason = !agentReady
    ? "请先启动本地代理"
    : !credentialsReady
      ? "请输入令牌 ID 和令牌密钥"
      : "";
  const installBlockedReason = !agentReady
    ? "请先启动本地代理"
    : !props.modalConnected
      ? "安装需要先连接 Modal，以下载运行时和模型文件"
      : !local?.supported_platform
        ? "当前系统不支持 Local SAM"
        : !local.hardware_eligible
          ? local.reason
          : !local.disk_eligible
            ? `磁盘空间不足，至少需要 ${(local.min_disk_mib / 1024).toFixed(1)} GiB 可用空间`
            : "";
  const installDisabled = Boolean(
    props.localBusy || local?.installing || installBlockedReason,
  );

  return (
    <div className="settings-backdrop" role="presentation" onMouseDown={props.onClose}>
      <section
        className="settings-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="settings-header">
          <div>
            <span className="eyebrow">Preferences</span>
            <h2 id="settings-title">设置</h2>
            <p>账号、推理服务和本机运行时都在这里管理。</p>
          </div>
          <button type="button" className="icon-button" onClick={props.onClose} aria-label="关闭设置">×</button>
        </header>

        <div className="settings-scroll">
          <section className="settings-section">
            <div className="settings-section-heading">
              <div className="settings-icon">M</div>
              <div><h3>Modal 云端</h3><p>模型发现、SAM 云端服务和 3D 生成</p></div>
              <span className={`status-pill ${props.modalConnected ? "success" : "neutral"}`}>
                <StatusDot active={props.modalConnected} />{props.modalConnected ? "已连接" : "未连接"}
              </span>
            </div>

            {props.modalConnected ? (
              <div className="settings-connected-row">
                <div><strong>当前会话已连接</strong><span>{props.modalMessage}</span></div>
                <div className="settings-actions">
                  {props.persistence.stored ? <button type="button" className="quiet-button" onClick={props.onForget}>删除已保存凭据</button> : null}
                  <button type="button" className="quiet-button danger-text" onClick={props.onDisconnect}>断开连接</button>
                </div>
              </div>
            ) : (
              <form className="settings-form" onSubmit={(event) => { event.preventDefault(); if (!connectDisabledReason) props.onConnect(); }}>
                <label>令牌 ID<input value={props.tokenId} onChange={(event) => props.setTokenId(event.target.value)} autoComplete="off" placeholder="ak-…" /></label>
                <label>令牌密钥<input type="password" value={props.tokenSecret} onChange={(event) => props.setTokenSecret(event.target.value)} autoComplete="off" placeholder="••••••••••••" /></label>
                <div className="settings-form-footer">
                  {props.persistence.supported ? <label className="remember"><input type="checkbox" checked={props.remember} onChange={(event) => props.setRemember(event.target.checked)} />使用 Windows 凭据管理器记住</label> : <span />}
                  <button className="primary-button" disabled={Boolean(connectDisabledReason)}>连接 Modal</button>
                </div>
                <span className={`settings-helper ${connectDisabledReason ? "warning" : ""}`} role="status">
                  {connectDisabledReason || props.modalMessage}
                </span>
              </form>
            )}
          </section>

          <section className="settings-section">
            <div className="settings-section-heading">
              <div className="settings-icon">AI</div>
              <div><h3>SAM 推理</h3><p>选择对象分割使用的计算位置</p></div>
              <div className="settings-heading-actions">
                <span className="status-pill neutral">{props.runtime?.sam.effective ?? "待检测"}</span>
                <button type="button" className="text-button" disabled={!agentReady || props.localBusy} onClick={props.onRefresh}>刷新状态</button>
              </div>
            </div>

            {props.runtime ? (
              <>
                <div className="segmented-control" aria-label="SAM Provider">
                  {SAM_MODES.map((mode) => (
                    <button
                      key={mode}
                      type="button"
                      className={props.runtime?.sam.mode === mode ? "active" : ""}
                      aria-pressed={props.runtime?.sam.mode === mode}
                      disabled={!agentReady || (mode === "local" && !local?.available)}
                      title={mode === "local" ? local?.reason : undefined}
                      onClick={() => props.onSamMode(mode)}
                    >
                      {mode === "auto" ? "自动" : mode === "cloud" ? "云端" : "本机"}
                    </button>
                  ))}
                </div>

                {local ? (
                  <div className="runtime-card">
                    <div className="runtime-card-head">
                      <div><strong>Local SAM Runtime</strong><span>{local.reason}</span></div>
                      <span className={`status-pill ${local.ready ? "success" : "neutral"}`}>{local.ready ? "运行中" : local.installed ? "已安装" : "未安装"}</span>
                    </div>
                    <div className="runtime-path"><span>存储位置</span><code title={local.root_path}>{local.root_path}</code></div>
                    <div className="runtime-requirements" aria-label="Local SAM 安装条件">
                      <span className={local.supported_platform ? "pass" : "fail"}>Windows {local.supported_platform ? "可用" : "不支持"}</span>
                      <span className={local.hardware_eligible ? "pass" : "fail"}>GPU {local.hardware_eligible ? "满足" : "不满足"}</span>
                      <span className={local.disk_eligible ? "pass" : "fail"}>磁盘 {local.disk_eligible ? "满足" : "不足"}</span>
                    </div>
                    {local.installing ? (
                      <div className="download-progress">
                        <div><span>{props.localProgress}</span><strong>{local.download_speed_bps ? `${(local.download_speed_bps / 1024 / 1024).toFixed(1)} MiB/s` : "处理中"}</strong></div>
                        <progress value={local.downloaded_bytes ?? 0} max={local.download_total_bytes ?? local.checkpoint_bytes} />
                      </div>
                    ) : null}
                    <div className="settings-actions runtime-actions">
                      {(!local.installed || local.update_available) ? <button type="button" className="primary-button" disabled={installDisabled} onClick={props.onInstall}>{local.installing ? "正在安装…" : local.update_available ? "更新运行时" : "安装运行时"}</button> : null}
                      {local.installed && !local.ready ? <button type="button" className="primary-button" disabled={props.localBusy || local.installing} onClick={props.onVerify}>启动并验证</button> : null}
                      <button type="button" className="quiet-button" disabled={!agentReady || props.localBusy || local.installing} onClick={props.onMigrate}>迁移目录</button>
                      {local.installed || local.update_available ? <button type="button" className="quiet-button danger-text" disabled={props.localBusy || local.installing} onClick={props.onUninstall}>卸载</button> : null}
                    </div>
                    {installBlockedReason && (!local.installed || local.update_available) ? <p className="control-explanation" role="status">{installBlockedReason}</p> : null}
                    {!local.available ? <p className="control-explanation">安装并验证完成后，才可选择“本机”模式；自动和云端模式始终可以切换。</p> : null}
                  </div>
                ) : null}
              </>
            ) : (
              <div className="settings-placeholder">
                <span>{agentReady ? "尚未取得本机能力状态。" : "启动本地代理后即可检测 SAM 能力。"}</span>
                <button type="button" className="quiet-button" disabled={!agentReady} onClick={props.onRefresh}>重新检测</button>
              </div>
            )}
          </section>

          <section className="settings-section compact">
            <div className="settings-section-heading">
              <div className="settings-icon">A</div>
              <div><h3>本地代理</h3><p>{props.inTauri ? props.agentMessage : "请通过桌面客户端运行"}</p></div>
              <button type="button" className="quiet-button" disabled={!props.inTauri || props.localBusy || Boolean(local?.installing)} onClick={props.onToggleAgent}>{props.agent?.running ? "停止" : "启动"}</button>
            </div>
          </section>
        </div>
      </section>
    </div>
  );
}
