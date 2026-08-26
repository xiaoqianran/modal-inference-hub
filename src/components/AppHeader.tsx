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
        <span className="project-context"><i /> {view === "gallery" ? "资产图库" : "3D 工作台"}</span>
        <div className="project-heading">
          <h1>{view === "gallery" ? "本地图库" : projectTitle || "新建 3D 项目"}</h1>
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
            <span><strong>本地服务</strong><small>{agentReady ? "已就绪" : "未连接"}</small></span>
          </span>
          <span className={`status-indicator ${modalConnected ? "online" : ""}`}>
            <i />
            <span><strong>Modal 云</strong><small>{modalConnected ? "已连接" : "未连接"}</small></span>
          </span>
        </div>
        {busy ? <span className="busy-label"><i />处理中</span> : null}
        <button type="button" className="icon-button control-center-button" onClick={onOpenSettings} aria-label="打开设置">
          设置
        </button>
      </div>
    </header>
  );
}
