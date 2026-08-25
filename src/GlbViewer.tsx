import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";

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

export default function GlbViewer({ url }: { url: string }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const resetViewRef = useRef<() => void>(() => undefined);
  const [message, setMessage] = useState("正在加载 3D…");

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

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

    scene.add(new THREE.HemisphereLight(0xffffff, 0x303040, 2.2));
    const key = new THREE.DirectionalLight(0xffffff, 3.2);
    key.position.set(4, 5, 6);
    scene.add(key);
    const fill = new THREE.DirectionalLight(0xaabfff, 1.6);
    fill.position.set(-4, 2, -3);
    scene.add(fill);

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
      if (performance.now() < renderUntil) frame = requestAnimationFrame(render);
      else frame = 0;
    };

    const invalidate = (duration = 0) => {
      renderUntil = Math.max(renderUntil, performance.now() + duration);
      if (!frame) frame = requestAnimationFrame(render);
    };

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
        camera.position.set(radius * 1.45, radius * 0.9, radius * 1.45);
        camera.updateProjectionMatrix();
        controls.target.set(0, 0, 0);
        controls.minDistance = radius * 0.3;
        controls.maxDistance = radius * 8;
        controls.update();
        resetViewRef.current = () => {
          camera.position.set(radius * 1.45, radius * 0.9, radius * 1.45);
          controls.target.set(0, 0, 0);
          controls.update();
          invalidate(400);
        };
        setMessage("");
        invalidate(400);
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
      if (model) disposeObject(model);
      renderer.dispose();
      renderer.domElement.remove();
      resetViewRef.current = () => undefined;
    };
  }, [url]);

  return (
    <div className="glb-viewer" ref={hostRef}>
      {message && <span className="viewer-message">{message}</span>}
      {!message ? <button type="button" className="viewer-reset" onClick={() => resetViewRef.current()}>重置视角</button> : null}
      <span className="viewer-hint">拖动旋转 · 滚轮缩放</span>
    </div>
  );
}
