type AppHeaderProps = {
  projectTitle?: string;
  view: "workbench" | "gallery";
  agentReady: boolean;
  modalConnected: boolean;
  busy: boolean;
  onChangeView: (view: "workbench" | "gallery") => void;
  onOpenSettings: () => void;
};

export default function AppHeader({
  projectTitle,
  view,
  agentReady,
  modalConnected,
  busy,
  onChangeView,
  onOpenSettings,
}: AppHeaderProps) {
  return (
    <header className="app-header">
      <div className="current-project">
        <span className="project-context"><i /> {view === "gallery" ? "ASSET LIBRARY" : "3D WORKBENCH"}</span>
        <div className="project-heading">
          <h1>{view === "gallery" ? "本地图库" : projectTitle || "新建 3D 项目"}</h1>
          <span>{view === "gallery" ? "IMAGE × MODEL" : "IMAGE → 3D"}</span>
        </div>
      </div>

      <nav className="header-view-switcher" aria-label="主视图">
        <button type="button" className={view === "workbench" ? "active" : ""} onClick={() => onChangeView("workbench")}>工作台</button>
        <button type="button" className={view === "gallery" ? "active" : ""} onClick={() => onChangeView("gallery")}>图库</button>
      </nav>

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
