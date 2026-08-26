import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, type DragEvent, type KeyboardEvent as ReactKeyboardEvent } from "react";
import {
  importLibraryImage,
  jobArtifactBlob,
  libraryThumbnailBlob,
  listLibrary,
  type AgentInfo,
  type LibraryGeneration,
  type LibraryItem,
  type LibraryPage,
  type ModelSpec,
} from "./agent";
import { useObjectUrl } from "./hooks/useObjectUrl";

const GlbViewer = lazy(() => import("./GlbViewer"));
const PAGE_SIZE = 48;
const EMPTY_PAGE: LibraryPage = { page: 1, page_size: PAGE_SIZE, total: 0, items: [] };
const activeStatuses = new Set([
  "submitting",
  "generating",
  "running",
  "connection_required",
  "cancel_requested",
]);

type GalleryProps = {
  agent: AgentInfo | null;
  models: ModelSpec[];
  onOpenProject: (projectId: string) => void | Promise<void>;
  onLibraryChanged: () => void | Promise<void>;
};

type ImportProgress = {
  total: number;
  completed: number;
  imported: number;
  duplicates: number;
  failed: number;
  current: string;
  done: boolean;
};

function dateLabel(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function modelAbbreviation(model: { id: string; name: string }) {
  const value = `${model.id} ${model.name}`.toLowerCase();
  if (value.includes("fastsam")) return "FS";
  if (value.includes("trellis")) return "TR";
  if (value.includes("hunyuan")) return "HY";
  if (value.includes("pixal")) return "PX";
  return model.name.replace(/[^a-z0-9]/gi, "").slice(0, 2).toUpperCase() || "3D";
}

function generationState(generation: LibraryGeneration | null) {
  if (!generation) return { label: "未生成", tone: "empty" };
  if (!generation.is_current) return { label: "历史结果", tone: "history" };
  if (generation.status === "submission_unknown") return { label: "待确认", tone: "warning" };
  if (generation.status === "succeeded" && generation.artifact_id) return { label: "已完成", tone: "complete" };
  if (activeStatuses.has(generation.status)) return { label: "生成中", tone: "active" };
  if (generation.status === "failed") return { label: "失败", tone: "error" };
  if (generation.status === "cancelled" || generation.status === "expired") {
    return { label: generation.status === "cancelled" ? "已取消" : "已过期", tone: "muted" };
  }
  return { label: "待生成", tone: "empty" };
}

function generationFor(item: LibraryItem, modelId: string) {
  return item.generations.find((generation) => generation.model === modelId) ?? null;
}

function displayModels(item: LibraryItem, models: ModelSpec[]) {
  const values = new Map(models.map((model) => [model.id, { id: model.id, name: model.name }]));
  for (const generation of item.generations) {
    if (!values.has(generation.model)) values.set(generation.model, { id: generation.model, name: generation.model });
  }
  return [...values.values()];
}

function Thumbnail({ agent, item, large = false }: { agent: AgentInfo; item: LibraryItem; large?: boolean }) {
  const [url, replaceUrl] = useObjectUrl();
  useEffect(() => {
    let active = true;
    replaceUrl(null);
    void libraryThumbnailBlob(agent, item.project.id)
      .then((blob) => { if (active) replaceUrl(blob); })
      .catch(() => undefined);
    return () => { active = false; };
  }, [agent, item.project.id, replaceUrl]);

  return (
    <div className={`gallery-thumbnail ${large ? "large" : ""}`}>
      {url ? <img src={url} alt={item.project.source_name} draggable={false} /> : <span>IMAGE</span>}
    </div>
  );
}

function GalleryCard({
  agent,
  item,
  models,
  selected,
  onSelect,
}: {
  agent: AgentInfo;
  item: LibraryItem;
  models: ModelSpec[];
  selected: boolean;
  onSelect: () => void;
}) {
  const modelList = displayModels(item, models);
  return (
    <button
      type="button"
      className={`gallery-card ${selected ? "selected" : ""}`}
      onClick={onSelect}
      aria-pressed={selected}
    >
      <Thumbnail agent={agent} item={item} />
      <span className="gallery-card-meta">
        <strong title={item.project.source_name}>{item.project.source_name}</strong>
        <small>{dateLabel(item.project.created_at)}</small>
      </span>
      <span className="gallery-model-summary" aria-label="模型结果">
        {modelList.slice(0, 6).map((model) => {
          const state = generationState(generationFor(item, model.id));
          return (
            <span key={model.id} className={`gallery-model-mark ${state.tone}`} title={`${model.name} · ${state.label}`}>
              {modelAbbreviation(model)}<i />
            </span>
          );
        })}
      </span>
    </button>
  );
}

function pageButtons(current: number, total: number) {
  if (total <= 7) return Array.from({ length: total }, (_, index) => index + 1);
  const values: Array<number | "…"> = [1];
  const start = Math.max(2, current - 1);
  const end = Math.min(total - 1, current + 1);
  if (start > 2) values.push("…");
  for (let page = start; page <= end; page += 1) values.push(page);
  if (end < total - 1) values.push("…");
  values.push(total);
  return values;
}

function Pagination({ page, total, onChange }: { page: number; total: number; onChange: (page: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const first = total ? (page - 1) * PAGE_SIZE + 1 : 0;
  const last = Math.min(total, page * PAGE_SIZE);
  return (
    <footer className="gallery-pagination">
      <span>{first}–{last} / {total}</span>
      <nav aria-label="图库分页">
        <button type="button" disabled={page <= 1} onClick={() => onChange(page - 1)}>‹</button>
        {pageButtons(page, pages).map((value, index) => value === "…" ? (
          <span key={`ellipsis-${index}`}>…</span>
        ) : (
          <button
            type="button"
            key={value}
            className={value === page ? "active" : ""}
            aria-current={value === page ? "page" : undefined}
            onClick={() => onChange(value)}
          >{value}</button>
        ))}
        <button type="button" disabled={page >= pages} onClick={() => onChange(page + 1)}>›</button>
      </nav>
    </footer>
  );
}

function Inspector({
  agent,
  item,
  models,
  onOpenProject,
}: {
  agent: AgentInfo;
  item: LibraryItem | null;
  models: ModelSpec[];
  onOpenProject: (projectId: string) => void | Promise<void>;
}) {
  const [modelId, setModelId] = useState("");
  const [resultUrl, replaceResultUrl] = useObjectUrl();
  const [viewerState, setViewerState] = useState<"idle" | "loading" | "ready" | "error">("idle");

  const availableModels = useMemo(() => item ? displayModels(item, models) : [], [item, models]);
  const generation = item ? generationFor(item, modelId) : null;
  const state = generationState(generation);

  useEffect(() => {
    if (!item) {
      setModelId("");
      return;
    }
    const preferred = item.generations.find((value) => value.is_current && value.status === "succeeded" && value.artifact_id)
      ?? item.generations.find((value) => value.is_current)
      ?? item.generations[0];
    setModelId(preferred?.model ?? availableModels[0]?.id ?? "");
  }, [item?.project.id, availableModels]);

  useEffect(() => {
    let active = true;
    replaceResultUrl(null);
    if (!generation?.job_id || !generation.artifact_id || generation.status !== "succeeded") {
      setViewerState("idle");
      return () => { active = false; };
    }
    setViewerState("loading");
    void jobArtifactBlob(agent, generation.job_id)
      .then((blob) => {
        if (!active) return;
        replaceResultUrl(blob);
        setViewerState("ready");
      })
      .catch(() => { if (active) setViewerState("error"); });
    return () => { active = false; };
  }, [agent, generation?.artifact_id, generation?.job_id, generation?.status, replaceResultUrl]);

  if (!item) {
    return (
      <aside className="gallery-inspector gallery-inspector-empty">
        <span>INSPECTOR</span>
        <strong>选择一张图片</strong>
        <p>查看它在不同 3D 模型下的生成状态与结果。</p>
      </aside>
    );
  }

  return (
    <aside className="gallery-inspector">
      <div className="gallery-inspector-head">
        <span>INSPECTOR</span>
        <strong>{item.project.source_name}</strong>
        <small>导入于 {dateLabel(item.project.created_at)}</small>
      </div>
      <Thumbnail agent={agent} item={item} large />
      <div className="gallery-source-meta">
        <span><small>尺寸</small><strong>{item.project.source?.width ?? "—"} × {item.project.source?.height ?? "—"}</strong></span>
        <span><small>状态</small><strong>{item.project.canonical_sha256 ? "已准备 Canonical" : "仅原图"}</strong></span>
      </div>

      <div className="gallery-inspector-section">
        <div className="gallery-section-title"><strong>3D 结果</strong><small>{availableModels.length} 个模型</small></div>
        <div className="gallery-model-tabs">
          {availableModels.map((model) => {
            const modelState = generationState(generationFor(item, model.id));
            return (
              <button
                type="button"
                key={model.id}
                className={`${model.id === modelId ? "active" : ""} ${modelState.tone}`}
                onClick={() => setModelId(model.id)}
              >
                <span><strong>{model.name}</strong><small>{modelState.label}</small></span><i />
              </button>
            );
          })}
        </div>
      </div>

      <div className="gallery-viewer">
        {resultUrl && viewerState === "ready" ? (
          <Suspense fallback={<div className="gallery-viewer-message">加载 3D 引擎…</div>}>
            <GlbViewer url={resultUrl} />
          </Suspense>
        ) : (
          <div className="gallery-viewer-message">
            <span>{viewerState === "loading" ? "LOADING GLB" : state.label.toUpperCase()}</span>
            <strong>{viewerState === "loading" ? "正在载入 3D 结果" : viewerState === "error" ? "本地产物不可用" : state.label}</strong>
            {!generation?.is_current && generation ? <small>该结果来自旧的 Canonical，可查看但不代表当前对象选择。</small> : null}
          </div>
        )}
      </div>

      {generation ? (
        <div className={`gallery-generation-note ${state.tone}`}>
          <span><strong>{state.label}</strong><small>{generation.error || dateLabel(generation.updated_at)}</small></span>
          {!generation.is_current ? <em>HISTORY</em> : null}
        </div>
      ) : null}

      <button type="button" className="primary-button gallery-open-workbench" onClick={() => { void onOpenProject(item.project.id); }}>
        在工作台中打开
      </button>
    </aside>
  );
}

export default function Gallery({ agent, models, onOpenProject, onLibraryChanged }: GalleryProps) {
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState<"created" | "updated">("created");
  const [library, setLibrary] = useState<LibraryPage>(EMPTY_PAGE);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const [dragging, setDragging] = useState(false);
  const [importProgress, setImportProgress] = useState<ImportProgress | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const gridRef = useRef<HTMLDivElement | null>(null);

  const selectedItem = library.items.find((item) => item.project.id === selectedProjectId) ?? null;

  useEffect(() => {
    if (!agent?.running) {
      setLibrary(EMPTY_PAGE);
      setSelectedProjectId(null);
      return;
    }
    let active = true;
    setLoading(true);
    setLoadError("");
    void listLibrary(agent, page, PAGE_SIZE, sort)
      .then((value) => {
        if (!active) return;
        setLibrary(value);
        setSelectedProjectId((current) => value.items.some((item) => item.project.id === current)
          ? current
          : value.items[0]?.project.id ?? null);
        if (value.total > 0 && value.items.length === 0 && page > 1) setPage(Math.max(1, Math.ceil(value.total / PAGE_SIZE)));
      })
      .catch((error) => { if (active) setLoadError(error instanceof Error ? error.message : String(error)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [agent, page, refreshKey, sort]);

  const importFiles = useCallback(async (files: File[]) => {
    if (!agent?.running || !files.length || importProgress && !importProgress.done) return;
    let imported = 0;
    let duplicates = 0;
    let failed = 0;
    setImportProgress({ total: files.length, completed: 0, imported, duplicates, failed, current: files[0]?.name ?? "", done: false });
    for (let index = 0; index < files.length; index += 1) {
      const file = files[index];
      try {
        const result = await importLibraryImage(agent, file);
        if (result.status === "duplicate") duplicates += 1;
        else imported += 1;
      } catch {
        failed += 1;
      }
      setImportProgress({
        total: files.length,
        completed: index + 1,
        imported,
        duplicates,
        failed,
        current: files[index + 1]?.name ?? file.name,
        done: index + 1 === files.length,
      });
    }
    if (imported > 0 && sort === "created") setPage(1);
    setRefreshKey((value) => value + 1);
    await onLibraryChanged();
  }, [agent, importProgress, onLibraryChanged, sort]);

  function handleDrop(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    setDragging(false);
    const files = Array.from(event.dataTransfer.files);
    if (files.length) void importFiles(files);
  }

  function navigateGrid(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (!selectedProjectId || !library.items.length) return;
    const index = library.items.findIndex((item) => item.project.id === selectedProjectId);
    if (index < 0) return;
    const width = gridRef.current?.clientWidth ?? 0;
    const columns = Math.max(1, Math.floor((width + 14) / (184 + 14)));
    let next = index;
    if (event.key === "ArrowLeft") next -= 1;
    else if (event.key === "ArrowRight") next += 1;
    else if (event.key === "ArrowUp") next -= columns;
    else if (event.key === "ArrowDown") next += columns;
    else return;
    event.preventDefault();
    const item = library.items[Math.min(library.items.length - 1, Math.max(0, next))];
    if (item) setSelectedProjectId(item.project.id);
  }

  return (
    <main
      className={`gallery-page ${dragging ? "dragging" : ""}`}
      onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
      onDragOver={(event) => event.preventDefault()}
      onDragLeave={(event) => { if (event.currentTarget === event.target) setDragging(false); }}
      onDrop={handleDrop}
    >
      <input
        ref={fileInputRef}
        className="gallery-file-input"
        type="file"
        accept="image/png,image/jpeg,image/webp,.png,.jpg,.jpeg,.webp"
        multiple
        onChange={(event) => {
          const files = Array.from(event.target.files ?? []);
          event.target.value = "";
          void importFiles(files);
        }}
      />

      <div className="gallery-toolbar">
        <div>
          <span>LOCAL ASSET LIBRARY</span>
          <h2>图库 <small>{library.total} 张图片</small></h2>
        </div>
        <div className="gallery-toolbar-actions">
          <label>
            <span>排序</span>
            <select value={sort} onChange={(event) => { setSort(event.target.value as "created" | "updated"); setPage(1); }}>
              <option value="created">最近导入</option>
              <option value="updated">最近更新</option>
            </select>
          </label>
          <button
            type="button"
            className="primary-button"
            disabled={!agent?.running || Boolean(importProgress && !importProgress.done)}
            onClick={() => fileInputRef.current?.click()}
          >+ 导入图片</button>
        </div>
      </div>

      <div className="gallery-body">
        <section className="gallery-browser">
          {loadError ? <div className="gallery-empty"><strong>图库读取失败</strong><span>{loadError}</span></div> : null}
          {!loadError && !loading && library.items.length === 0 ? (
            <div className="gallery-empty">
              <span>DROP IMAGES HERE</span>
              <strong>图库还是空的</strong>
              <p>导入 PNG、JPEG 或 WebP。图片只保存在本地，生成 3D 时才上传 Canonical。</p>
              <button type="button" className="primary-button" onClick={() => fileInputRef.current?.click()}>导入第一批图片</button>
            </div>
          ) : null}
          {loading && !library.items.length ? <div className="gallery-empty"><strong>正在读取图库…</strong></div> : null}
          {library.items.length ? (
            <div className="gallery-grid" ref={gridRef} tabIndex={0} onKeyDown={navigateGrid}>
              {library.items.map((item) => agent?.running ? (
                <GalleryCard
                  key={item.project.id}
                  agent={agent}
                  item={item}
                  models={models}
                  selected={item.project.id === selectedProjectId}
                  onSelect={() => setSelectedProjectId(item.project.id)}
                />
              ) : null)}
            </div>
          ) : null}
          <Pagination page={page} total={library.total} onChange={setPage} />
        </section>
        {agent?.running ? <Inspector agent={agent} item={selectedItem} models={models} onOpenProject={onOpenProject} /> : null}
      </div>

      {dragging ? <div className="gallery-drop-overlay"><span>释放以导入图片</span><small>PNG · JPEG · WEBP</small></div> : null}
      {importProgress ? (
        <div className={`gallery-import-shelf ${importProgress.done ? "done" : ""}`}>
          <div>
            <span><strong>{importProgress.done ? "导入完成" : `正在导入 ${importProgress.completed} / ${importProgress.total}`}</strong><small>{importProgress.done ? `${importProgress.imported} 张已导入 · ${importProgress.duplicates} 张重复 · ${importProgress.failed} 张失败` : importProgress.current}</small></span>
            {importProgress.done ? <button type="button" onClick={() => setImportProgress(null)}>关闭</button> : null}
          </div>
          <i><span style={{ width: `${Math.round(importProgress.completed / importProgress.total * 100)}%` }} /></i>
        </div>
      ) : null}
    </main>
  );
}
