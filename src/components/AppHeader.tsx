type AppHeaderProps = {
  projectTitle?: string;
  agentReady: boolean;
  modalConnected: boolean;
  busy: boolean;
  onOpenSettings: () => void;
};

export default function AppHeader({
  projectTitle,
  agentReady,
  modalConnected,
  busy,
  onOpenSettings,
}: AppHeaderProps) {
  return (
    <header className="app-header">
      <div className="current-project">
        <span>当前工作区</span>
        <h1>{projectTitle || "新建 3D 项目"}</h1>
      </div>
      <div className="header-actions" aria-label="运行状态">
        <span className={`status-indicator ${agentReady ? "online" : ""}`}>
          <i />本地服务
        </span>
        <span className={`status-indicator ${modalConnected ? "online" : ""}`}>
          <i />Modal
        </span>
        {busy ? <span className="busy-label">处理中</span> : null}
        <button type="button" className="icon-button" onClick={onOpenSettings} aria-label="打开设置">
          设置
        </button>
      </div>
    </header>
  );
}
