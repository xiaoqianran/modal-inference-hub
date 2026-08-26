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
        <span className="project-context"><i /> 3D WORKBENCH</span>
        <div className="project-heading">
          <h1>{projectTitle || "新建 3D 项目"}</h1>
          <span>IMAGE → 3D</span>
        </div>
      </div>

      <div className="header-actions" aria-label="运行状态">
        <div className="runtime-cluster">
          <span className={`status-indicator ${agentReady ? "online" : ""}`}>
            <i />
            <span><strong>Local Agent</strong><small>{agentReady ? "Ready" : "Offline"}</small></span>
          </span>
          <span className={`status-indicator ${modalConnected ? "online" : ""}`}>
            <i />
            <span><strong>Modal Cloud</strong><small>{modalConnected ? "Connected" : "Disconnected"}</small></span>
          </span>
        </div>
        {busy ? <span className="busy-label"><i />处理中</span> : null}
        <button type="button" className="icon-button control-center-button" onClick={onOpenSettings} aria-label="打开控制中心">
          控制中心
        </button>
      </div>
    </header>
  );
}
