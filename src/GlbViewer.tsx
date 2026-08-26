import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";

type ViewPreset = "home" | "front" | "side" | "top";
type ViewerStats = { meshes: number; triangles: number; materials: number };

function disposeMaterial(material: THREE.Material) {
  for (const value of Object.values(material)) {
    if (value instanceof THREE.Texture) value.dispose();
  }
  material.dispose();
}

function disposeObject(root: THREE.Object3D) {
  root.traverse((child) => {
    if (!(child instanceof THREE.Mesh)) return;
    child.geometry?.dispose();
    const materials = Array.isArray(child.material) ? child.material : [child.material];
    materials.forEach(disposeMaterial);
  });
}

function modelStats(root: THREE.Object3D): ViewerStats {
  let meshes = 0;
  let triangles = 0;
  const materials = new Set<THREE.Material>();
  root.traverse((child) => {
    if (!(child instanceof THREE.Mesh)) return;
    meshes += 1;
    const position = child.geometry.getAttribute("position");
    triangles += child.geometry.index
      ? Math.floor(child.geometry.index.count / 3)
      : Math.floor((position?.count ?? 0) / 3);
    const meshMaterials = Array.isArray(child.material) ? child.material : [child.material];
    meshMaterials.forEach((material) => materials.add(material));
  });
  return { meshes, triangles, materials: materials.size };
}

function compactNumber(value: number) {
  return new Intl.NumberFormat("zh-CN", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

export default function GlbViewer({ url }: { url: string }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const controlsRef = useRef<OrbitControls | null>(null);
  const gridRef = useRef<THREE.GridHelper | null>(null);
  const setViewRef = useRef<(preset: ViewPreset) => void>(() => undefined);
  const invalidateRef = useRef<(duration?: number) => void>(() => undefined);
  const captureRef = useRef<() => Promise<Blob | null>>(async () => null);
  const feedbackTimerRef = useRef<number | null>(null);
  const [message, setMessage] = useState("正在加载 3D…");
  const [autoRotate, setAutoRotate] = useState(false);
  const [gridVisible, setGridVisible] = useState(true);
  const [expanded, setExpanded] = useState(false);
  const [snapshotStatus, setSnapshotStatus] = useState("");
  const [stats, setStats] = useState<ViewerStats | null>(null);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    setMessage("正在加载 3D…");
    setStats(null);
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(42, 1, 0.01, 1000);
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "high-performance" });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.1;
    host.append(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.06;
    controls.enablePan = false;
    controls.autoRotate = autoRotate;
    controls.autoRotateSpeed = 1.35;
    controlsRef.current = controls;

    scene.add(new THREE.HemisphereLight(0xffffff, 0x303040, 2.2));
    const key = new THREE.DirectionalLight(0xffffff, 3.2);
    key.position.set(4, 5, 6);
    scene.add(key);
    const fill = new THREE.DirectionalLight(0xaabfff, 1.6);
    fill.position.set(-4, 2, -3);
    scene.add(fill);

    const grid = new THREE.GridHelper(4, 16, 0x6f82b8, 0x30394a);
    grid.position.y = -1;
    grid.material.transparent = true;
    grid.material.opacity = 0.26;
    grid.visible = gridVisible;
    gridRef.current = grid;
    scene.add(grid);

    let model: THREE.Object3D | null = null;
    let frame = 0;
    let disposed = false;
    let renderUntil = 0;

    const render = () => {
      if (disposed || document.hidden) {
        frame = 0;
        return;
      }
      controls.update();
      renderer.render(scene, camera);
      if (controls.autoRotate || performance.now() < renderUntil) frame = requestAnimationFrame(render);
      else frame = 0;
    };

    const invalidate = (duration = 0) => {
      renderUntil = Math.max(renderUntil, performance.now() + duration);
      if (!frame) frame = requestAnimationFrame(render);
    };
    invalidateRef.current = invalidate;

    controls.addEventListener("start", () => invalidate(300));
    controls.addEventListener("change", () => invalidate(250));
    controls.addEventListener("end", () => invalidate(500));

    const resize = () => {
      const width = Math.max(host.clientWidth, 1);
      const height = Math.max(host.clientHeight, 1);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      renderer.setSize(width, height, false);
      invalidate();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(host);
    resize();

    captureRef.current = async () => {
      if (disposed) return null;
      const capture = new THREE.WebGLRenderer({
        antialias: true,
        alpha: true,
        preserveDrawingBuffer: true,
      });
      try {
        const width = Math.max(host.clientWidth, 1);
        const height = Math.max(host.clientHeight, 1);
        const pixelRatio = Math.min(
          window.devicePixelRatio,
          2,
          4096 / Math.max(width, height),
        );
        capture.setPixelRatio(pixelRatio);
        capture.outputColorSpace = THREE.SRGBColorSpace;
        capture.toneMapping = THREE.ACESFilmicToneMapping;
        capture.toneMappingExposure = renderer.toneMappingExposure;
        capture.setClearColor(0x111620, 1);
        capture.setSize(width, height, false);
        capture.render(scene, camera);
        return await new Promise<Blob | null>((resolve) => capture.domElement.toBlob(resolve, "image/png"));
      } finally {
        capture.dispose();
        capture.forceContextLoss();
      }
    };

    new GLTFLoader().load(
      url,
      (gltf) => {
        if (disposed) {
          disposeObject(gltf.scene);
          return;
        }
        model = gltf.scene;
        scene.add(model);

        const box = new THREE.Box3().setFromObject(model);
        const center = box.getCenter(new THREE.Vector3());
        const size = box.getSize(new THREE.Vector3());
        model.position.sub(center);
        const radius = Math.max(size.length() * 0.5, 0.01);
        camera.near = Math.max(radius / 100, 0.001);
        camera.far = radius * 100;
        camera.updateProjectionMatrix();
        controls.target.set(0, 0, 0);
        controls.minDistance = radius * 0.3;
        controls.maxDistance = radius * 8;

        grid.scale.setScalar(Math.max(radius, 0.5));
        grid.position.y = -Math.max(size.y * 0.5, 0.01);

        const setView = (preset: ViewPreset) => {
          const distance = radius * 2.05;
          const positions: Record<ViewPreset, [number, number, number]> = {
            home: [radius * 1.45, radius * 0.9, radius * 1.45],
            front: [0, radius * 0.08, distance],
            side: [distance, radius * 0.08, 0],
            top: [0, distance, radius * 0.02],
          };
          camera.position.set(...positions[preset]);
          controls.target.set(0, 0, 0);
          controls.update();
          invalidate(420);
        };
        setViewRef.current = setView;
        setView("home");
        setStats(modelStats(model));
        setMessage("");
      },
      (event) => {
        if (event.total > 0) setMessage(`正在加载 3D · ${Math.floor((event.loaded / event.total) * 100)}%`);
      },
      () => setMessage("GLB 预览加载失败"),
    );

    const handleVisibility = () => {
      if (!document.hidden) invalidate(250);
    };
    document.addEventListener("visibilitychange", handleVisibility);
    invalidate();

    return () => {
      disposed = true;
      cancelAnimationFrame(frame);
      document.removeEventListener("visibilitychange", handleVisibility);
      observer.disconnect();
      controls.dispose();
      controlsRef.current = null;
      if (model) disposeObject(model);
      grid.geometry.dispose();
      const gridMaterials = Array.isArray(grid.material) ? grid.material : [grid.material];
      gridMaterials.forEach((material) => material.dispose());
      gridRef.current = null;
      renderer.dispose();
      renderer.domElement.remove();
      setViewRef.current = () => undefined;
      invalidateRef.current = () => undefined;
      captureRef.current = async () => null;
    };
  }, [url]);

  useEffect(() => {
    if (!expanded) return;
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setExpanded(false);
    };
    window.addEventListener("keydown", handleEscape);
    return () => window.removeEventListener("keydown", handleEscape);
  }, [expanded]);

  useEffect(() => () => {
    if (feedbackTimerRef.current !== null) window.clearTimeout(feedbackTimerRef.current);
  }, []);

  const toggleAutoRotate = () => {
    setAutoRotate((current) => {
      const next = !current;
      if (controlsRef.current) controlsRef.current.autoRotate = next;
      invalidateRef.current(250);
      return next;
    });
  };

  const toggleGrid = () => {
    setGridVisible((current) => {
      const next = !current;
      if (gridRef.current) gridRef.current.visible = next;
      invalidateRef.current(250);
      return next;
    });
  };

  const saveSnapshot = async () => {
    if (snapshotStatus) return;
    if (feedbackTimerRef.current !== null) window.clearTimeout(feedbackTimerRef.current);
    setSnapshotStatus("正在导出截图…");
    try {
      const blob = await captureRef.current();
      if (!blob) throw new Error("当前画面尚未准备好");
      const href = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = href;
      anchor.download = `modal-3d-${new Date().toISOString().replace(/[:.]/g, "-")}.png`;
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(href), 1_000);
      setSnapshotStatus("PNG 截图已开始下载");
    } catch (error) {
      setSnapshotStatus(error instanceof Error ? error.message : "截图失败");
    } finally {
      feedbackTimerRef.current = window.setTimeout(() => {
        setSnapshotStatus("");
        feedbackTimerRef.current = null;
      }, 2_200);
    }
  };

  return (
    <div className={`glb-viewer ${expanded ? "expanded" : ""}`} ref={hostRef}>
      {message ? <span className="viewer-message">{message}</span> : null}
      {!message ? (
        <>
          <div className="viewer-toolbar" aria-label="3D 视图工具">
            <div className="viewer-view-presets">
              <button type="button" onClick={() => setViewRef.current("home")}>透视</button>
              <button type="button" onClick={() => setViewRef.current("front")}>正面</button>
              <button type="button" onClick={() => setViewRef.current("side")}>侧面</button>
              <button type="button" onClick={() => setViewRef.current("top")}>顶部</button>
            </div>
            <div className="viewer-display-toggles">
              <button type="button" className={autoRotate ? "active" : ""} aria-pressed={autoRotate} onClick={toggleAutoRotate}>旋转</button>
              <button type="button" className={gridVisible ? "active" : ""} aria-pressed={gridVisible} onClick={toggleGrid}>网格</button>
              <button type="button" disabled={Boolean(snapshotStatus)} onClick={() => void saveSnapshot()}>截图</button>
              <button type="button" className={expanded ? "active" : ""} aria-pressed={expanded} onClick={() => setExpanded((value) => !value)}>
                {expanded ? "退出全屏" : "全屏"}
              </button>
            </div>
          </div>
          {stats ? (
            <div className="viewer-stats">
              <span>{stats.meshes} meshes</span>
              <span>{compactNumber(stats.triangles)} tris</span>
              <span>{stats.materials} materials</span>
            </div>
          ) : null}
          {snapshotStatus ? <span className="viewer-feedback" role="status">{snapshotStatus}</span> : null}
        </>
      ) : null}
      <span className="viewer-hint">拖动旋转 · 滚轮缩放</span>
    </div>
  );
}
