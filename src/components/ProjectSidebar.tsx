import type { AgentInfo, Project } from "../agent";
import { isProjectGenerationActive } from "../generationState";
import { useProjectThumbnails } from "../hooks/useProjectThumbnails";

const statusLabels: Record<Project["status"], string> = {
  draft: "待抠图",
  segmented: "旧项目",
  ready: "可生成",
  submitting: "提交中",
  submission_unknown: "待确认",
  generating: "提交中",
  running: "生成中",
  connection_required: "等待连接",
  cancel_requested: "取消中",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已取消",
  expired: "已过期",
};

function relativeTime(value: string) {
  const time = new Date(value).getTime();
  if (!Number.isFinite(time)) return "刚刚";
  const minutes = Math.max(0, Math.floor((Date.now() - time) / 60_000));
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days} 天前`;
  return new Date(value).toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}

function assetStage(project: Project) {
  if (project.artifact_id) return "GLB";
  if (project.canonical_id) return "CANONICAL";
  return "SOURCE";
}

function statusTone(status: Project["status"]) {
  if (status === "succeeded") return "success";
  if (status === "failed" || status === "submission_unknown") return "warning";
  if (isProjectGenerationActive(status)) return "active";
  return "neutral";
}

type ProjectSidebarProps = {
  agent: AgentInfo | null;
  projects: Project[];
  activeProjectId?: string;
  busy: boolean;
  onSelect: (projectId: string) => void;
  onDelete: (project: Project) => void;
};

export default function ProjectSidebar({
  agent,
  projects,
  activeProjectId,
  busy,
  onSelect,
  onDelete,
}: ProjectSidebarProps) {
  const thumbnails = useProjectThumbnails(agent, projects);
  const activeCount = projects.filter((item) => isProjectGenerationActive(item.status)).length;
  const completedCount = projects.filter((item) => item.status === "succeeded").length;
  const readyCount = projects.filter((item) => item.status === "ready").length;

  return (
    <aside className="project-sidebar">
      <div className="app-brand">
        <span className="brand-mark">M3</span>
        <div><strong>modal-3D</strong><small>STUDIO</small></div>
      </div>

      <div className="sidebar-overview" aria-label="项目概览">
        <span><small>PROJECTS</small><strong>{projects.length}</strong></span>
        <span><small>READY</small><strong>{readyCount}</strong></span>
        <span className={activeCount ? "active" : ""}><small>ACTIVE</small><strong>{activeCount}</strong></span>
        <span className={completedCount ? "success" : ""}><small>DONE</small><strong>{completedCount}</strong></span>
      </div>

      <div className="sidebar-title">
        <span>项目库</span>
        <small>最近更新</small>
      </div>

      <div className="recent-projects">
        {projects.length ? projects.map((item) => {
          const active = item.id === activeProjectId;
          const locked = isProjectGenerationActive(item.status);
          const tone = statusTone(item.status);
          return (
            <div key={item.id} className={`recent-project ${active ? "active" : ""}`}>
              <button type="button" className="project-open" disabled={busy} onClick={() => onSelect(item.id)}>
                <span className={`project-thumbnail ${tone}`}>
                  {thumbnails[item.id] ? (
                    <img src={thumbnails[item.id]} alt="" />
                  ) : (
                    <strong>{item.title.slice(0, 1).toUpperCase()}</strong>
                  )}
                  <i />
                </span>
                <span className="project-copy">
                  <span className="project-title-line">
                    <strong>{item.title}</strong>
                    {active ? <em>CURRENT</em> : null}
                  </span>
                  <span className="project-meta-line">
                    <small className={`project-status ${tone}`}><i />{statusLabels[item.status]}</small>
                    <small>{assetStage(item)}</small>
                    <small>{relativeTime(item.updated_at)}</small>
                  </span>
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
            <span>导入一张图片即可开始本地 3D 工作流。</span>
          </div>
        )}
      </div>

      <div className="shortcut-hints" aria-label="快捷键">
        <div><span>工作流</span><kbd>Alt 1</kbd><kbd>Alt 2</kbd></div>
        <div><span>生成</span><kbd>Ctrl ↵</kbd></div>
        <div><span>设置</span><kbd>Ctrl ,</kbd></div>
      </div>

      <div className="sidebar-footer">
        <span className="local-badge"><i />Local-first</span>
        <small>原图仅保存在本机 · Canonical 按需上传</small>
      </div>
    </aside>
  );
}
