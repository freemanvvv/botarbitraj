/**
 * MeshViewer — просмотр текстурированного 3D-меша (OBJ + MTL + текстуры) из
 * режима «Местность» (OpenDroneMap) прямо в браузере на three.js.
 *
 * Загружает .obj → .mtl → текстуры пофайлово через /api/gsplat/mesh-file.
 * ODM-меш геопривязан (координаты UTM, огромные смещения), поэтому геометрию
 * центрируем в ноль и подбираем камеру по габаритам bbox.
 */
import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OBJLoader } from "three/examples/jsm/loaders/OBJLoader.js";
import { MTLLoader } from "three/examples/jsm/loaders/MTLLoader.js";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

const API = import.meta.env.DEV ? "http://localhost:8765" : "";

interface Props {
  jobId: string;
  objName: string;
}

export default function MeshViewer({ jobId, objName }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const controlsRef = useRef<OrbitControls | null>(null);
  const homeRef = useRef<{ cam: THREE.Vector3; tgt: THREE.Vector3 } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || !jobId || !objName) return;
    setLoading(true);
    setError(null);

    let destroyed = false;
    let raf = 0;
    const base = `${API}/api/gsplat/mesh-file/${jobId}/`;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0f172a);

    const camera = new THREE.PerspectiveCamera(
      55, container.clientWidth / container.clientHeight, 0.01, 100000
    );

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(window.devicePixelRatio);
    renderer.setSize(container.clientWidth, container.clientHeight);
    container.appendChild(renderer.domElement);

    // Свет: меш ODM обычно с текстурами, но добавим и освещение на случай
    // отсутствия материалов (голая геометрия).
    scene.add(new THREE.AmbientLight(0xffffff, 0.9));
    const dir = new THREE.DirectionalLight(0xffffff, 0.8);
    dir.position.set(1, 2, 1);
    scene.add(dir);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controlsRef.current = controls;

    const onResize = () => {
      if (!container) return;
      camera.aspect = container.clientWidth / container.clientHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(container.clientWidth, container.clientHeight);
    };
    window.addEventListener("resize", onResize);

    const frameObject = (obj: THREE.Object3D) => {
      // Центрируем в ноль и ставим камеру на расстоянии ~1.8 диагонали bbox.
      const box = new THREE.Box3().setFromObject(obj);
      const size = box.getSize(new THREE.Vector3());
      const center = box.getCenter(new THREE.Vector3());
      obj.position.sub(center);            // геометрия → в начало координат
      const diag = size.length() || 1;
      const d = diag * 0.9;
      camera.position.set(d, d * 0.7, d);
      camera.near = diag / 1000;
      camera.far = diag * 1000;
      camera.updateProjectionMatrix();
      controls.target.set(0, 0, 0);
      controls.update();
      homeRef.current = {
        cam: camera.position.clone(),
        tgt: controls.target.clone(),
      };
    };

    const addAndStart = (obj: THREE.Object3D) => {
      if (destroyed) return;
      scene.add(obj);
      frameObject(obj);
      setLoading(false);
      const animate = () => {
        if (destroyed) return;
        controls.update();
        renderer.render(scene, camera);
        raf = requestAnimationFrame(animate);
      };
      animate();
    };

    const loadObj = (materials?: MTLLoader.MaterialCreator) => {
      const objLoader = new OBJLoader();
      if (materials) objLoader.setMaterials(materials);
      objLoader.setPath(base);
      objLoader.load(
        objName,
        (obj) => addAndStart(obj),
        undefined,
        (e) => {
          if (!destroyed) {
            setError(`Не удалось загрузить меш: ${(e as any)?.message || e}`);
            setLoading(false);
          }
        }
      );
    };

    // Сначала пробуем .mtl (текстуры), имя выводим из имени .obj; если mtl нет —
    // грузим голую геометрию.
    const mtlName = objName.replace(/\.obj$/i, ".mtl");
    const mtlLoader = new MTLLoader();
    mtlLoader.setPath(base);
    mtlLoader.setResourcePath(base);
    mtlLoader.load(
      mtlName,
      (materials) => { materials.preload(); loadObj(materials); },
      undefined,
      () => loadObj()   // mtl нет/битый → без материалов
    );

    return () => {
      destroyed = true;
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", onResize);
      controls.dispose();
      renderer.dispose();
      scene.traverse((o: any) => {
        if (o.geometry) o.geometry.dispose?.();
        if (o.material) {
          const mats = Array.isArray(o.material) ? o.material : [o.material];
          mats.forEach((m: any) => { m.map?.dispose?.(); m.dispose?.(); });
        }
      });
      if (renderer.domElement.parentNode === container) {
        container.removeChild(renderer.domElement);
      }
    };
  }, [jobId, objName]);

  const resetView = () => {
    const c = controlsRef.current, h = homeRef.current;
    if (!c || !h) return;
    c.object.position.copy(h.cam);
    c.target.copy(h.tgt);
    c.update();
  };

  const btn: React.CSSProperties = {
    width: 34, height: 34, borderRadius: 8, cursor: "pointer",
    border: "1px solid var(--border2)", background: "rgba(15,23,42,0.9)",
    color: "#fff", fontSize: "1rem", fontFamily: "inherit",
    display: "flex", alignItems: "center", justifyContent: "center", lineHeight: 1,
  };

  return (
    <div style={{ position: "relative", width: "100%", height: "100%" }}>
      <div ref={containerRef} style={{ width: "100%", height: "100%", background: "#0f172a" }} />

      {!error && !loading && (
        <>
          <div style={{
            position: "absolute", bottom: 44, right: 12, zIndex: 6,
            display: "flex", flexDirection: "column", gap: 6, alignItems: "center",
          }}>
            <button style={btn} title="Сбросить вид" onClick={resetView}>⟲</button>
          </div>
          <div style={{
            position: "absolute", bottom: 10, left: 12, zIndex: 6,
            fontSize: "0.7rem", color: "rgba(255,255,255,0.75)",
            background: "rgba(15,23,42,0.7)", padding: "4px 8px", borderRadius: 6,
          }}>
            ЛКМ — вращать · колесо — зум · ПКМ / 2 пальца — двигать
          </div>
        </>
      )}
      {loading && !error && (
        <div style={{
          position: "absolute", inset: 0, display: "flex",
          alignItems: "center", justifyContent: "center",
          background: "rgba(15,23,42,0.85)", flexDirection: "column", gap: 12,
        }}>
          <div style={{ fontSize: "2rem" }}>⏳</div>
          <div style={{ color: "var(--text2)" }}>Загрузка 3D-меша...</div>
        </div>
      )}
      {error && (
        <div style={{
          position: "absolute", inset: 0, display: "flex",
          alignItems: "center", justifyContent: "center",
          flexDirection: "column", gap: 12, padding: 24,
        }}>
          <div style={{ fontSize: "2rem" }}>⚠️</div>
          <pre style={{
            color: "var(--danger)", fontSize: "0.8rem",
            whiteSpace: "pre-wrap", textAlign: "center", maxWidth: 500,
          }}>{error}</pre>
        </div>
      )}
    </div>
  );
}
