import type { Project } from "../agent";

const activeStatuses = new Set<Project["status"]>([
  "submitting",
  "generating",
  "running",
  "connection_required",
  "cancel_requested",
]);

const statusLabels: Record<Project["status"], string> = {
  draft: "待抠图",
  segmented: "旧项目",
  ready: "可生成",
  submitting: "提交中",
  generating: "提交中",
  running: "生成中",
  connection_required: "等待连接",
  cancel_requested: "取消中",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已取消",
  expired: "已过期",
};

type ProjectSidebarProps = {
  projects: Project[];
  activeProjectId?: string;
  busy: boolean;
  onSelect: (projectId: string) => void;
  onDelete: (project: Project) => void;
};

export default function ProjectSidebar({
  projects,
  activeProjectId,
  busy,
  onSelect,
  onDelete,
}: ProjectSidebarProps) {
  return (
    <aside className="project-sidebar">
      <div className="app-brand">
        <span className="brand-mark">M3</span>
        <div><strong>modal-3D</strong><small>STUDIO</small></div>
      </div>

      <div className="sidebar-title">
        <span>最近项目</span>
        <strong>{projects.length}</strong>
      </div>

      <div className="recent-projects">
        {projects.length ? projects.map((item) => {
          const active = item.id === activeProjectId;
          const locked = activeStatuses.has(item.status);
          return (
            <div key={item.id} className={`recent-project ${active ? "active" : ""}`}>
              <button type="button" className="project-open" disabled={busy} onClick={() => onSelect(item.id)}>
                <span className="project-thumbnail">{item.title.slice(0, 1).toUpperCase()}</span>
                <span className="project-copy">
                  <strong>{item.title}</strong>
                  <small>{statusLabels[item.status]}</small>
                </span>
              </button>
              <button
                type="button"
                className="delete-project"
                disabled={busy || locked}
                onClick={() => onDelete(item)}
                aria-label={`删除项目 ${item.title}`}
                title={locked ? "任务活动期间不能删除" : "删除项目"}
              >
                ×
              </button>
            </div>
          );
        }) : (
          <div className="sidebar-empty">
            <strong>还没有项目</strong>
            <span>导入一张图片即可开始。</span>
          </div>
        )}
      </div>

      <div className="sidebar-footer">
        <span className="local-badge"><i />Local-first</span>
        <small>原图仅保存在本机</small>
      </div>
    </aside>
  );
}
