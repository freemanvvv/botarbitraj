import { useEffect, useRef } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

const API = "http://localhost:8765";

interface Props {
  filename: string | null;
}

export default function ThreeViewer({ filename }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current || !filename) return;

    const container = containerRef.current;
    const width = container.clientWidth;
    const height = container.clientHeight;

    // Scene
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0f172a);

    // Camera
    const camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 1000);
    camera.position.set(15, 10, 15);

    // Renderer
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    container.appendChild(renderer.domElement);

    // Controls
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.target.set(0, 2, 0);

    // Lights
    scene.add(new THREE.AmbientLight(0x8888ff, 0.5));
    const dir = new THREE.DirectionalLight(0xffffff, 1.0);
    dir.position.set(10, 20, 10);
    scene.add(dir);
    scene.add(new THREE.HemisphereLight(0x88ccff, 0x444422, 0.6));

    // Grid с размерными метками
    const gridSize = 30;
    const gridDiv = 20;
    const grid = new THREE.GridHelper(gridSize, gridDiv, 0x38bdf8, 0x334155);
    grid.position.y = 0;
    scene.add(grid);

    // Размерные линии осей
    const axisLen = 8;
    // Ось X (красная)
    const xMat = new THREE.LineBasicMaterial({ color: 0xff4444, linewidth: 2 });
    const xGeo = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(0, 0.02, 0), new THREE.Vector3(axisLen, 0.02, 0)
    ]);
    scene.add(new THREE.Line(xGeo, xMat));

    // Ось Y (зелёная)
    const yMat = new THREE.LineBasicMaterial({ color: 0x44ff44, linewidth: 2 });
    const yGeo = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(0, 0.02, 0), new THREE.Vector3(0, 0.02, axisLen)
    ]);
    scene.add(new THREE.Line(yGeo, yMat));

    // Ось Z (синяя)
    const zMat = new THREE.LineBasicMaterial({ color: 0x4488ff, linewidth: 2 });
    const zGeo = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(0, 0.02, 0), new THREE.Vector3(0, axisLen, 0)
    ]);
    scene.add(new THREE.Line(zGeo, zMat));

    // Метки осей (X, Y, Z)
    function makeLabel(text, x, y, z, color) {
      const canvas = document.createElement('canvas');
      canvas.width = 64; canvas.height = 64;
      const ctx = canvas.getContext('2d');
      ctx.fillStyle = 'transparent'; ctx.fillRect(0, 0, 64, 64);
      ctx.font = 'bold 48px system-ui';
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillStyle = color; ctx.fillText(text, 32, 32);
      const tex = new THREE.CanvasTexture(canvas);
      const mat = new THREE.SpriteMaterial({ map: tex, transparent: true, depthTest: false });
      const sprite = new THREE.Sprite(mat);
      sprite.position.set(x, y, z);
      sprite.scale.set(2, 2, 1);
      scene.add(sprite);
    }
    makeLabel('X', axisLen + 0.8, 0.02, 0, '#ff6666');
    makeLabel('Y', 0, 0.02, axisLen + 0.8, '#66ff66');
    makeLabel('Z', 0, axisLen + 0.8, 0, '#6688ff');

    // Load geometry from backend API
    const url = `${API}/api/model/view/${filename}`;

    fetch(url)
      .then((r) => r.json())
      .then((data) => {
        if (!data.elements?.length) return;

        const colorMap: Record<string, number> = {
          IfcWall: 0x60a5fa,
          IfcSlab: 0x94a3b8,
          IfcWindow: 0x22d3ee,
          IfcDoor: 0xf59e0b,
          IfcRoof: 0xdc2626,
        };

        const group = new THREE.Group();

        data.elements.forEach((elem: any) => {
          if (!elem.vertices?.length || !elem.faces?.length) return;

          const geo = new THREE.BufferGeometry();
          geo.setAttribute("position", new THREE.Float32BufferAttribute(elem.vertices, 3));
          geo.setIndex(new THREE.BufferAttribute(new Uint32Array(elem.faces), 1));
          geo.computeVertexNormals();

          const color = new THREE.Color(colorMap[elem.type] || 0x8b5cf6);
          const isGlass = elem.type === "IfcWindow";
          const mat = new THREE.MeshStandardMaterial({
            color,
            roughness: isGlass ? 0.05 : 0.7,
            metalness: isGlass ? 0.1 : 0.1,
            transparent: isGlass,
            opacity: isGlass ? 0.35 : 1.0,
            side: THREE.DoubleSide,
          });

          const mesh = new THREE.Mesh(geo, mat);
          mesh.castShadow = true;
          mesh.receiveShadow = true;
          mesh.userData.type = elem.type;
          mesh.userData.name = elem.name;
          group.add(mesh);
        });

        scene.add(group);

        // Auto-fit camera
        const box = new THREE.Box3().setFromObject(group);
        if (!box.isEmpty()) {
          const center = box.getCenter(new THREE.Vector3());
          const size = box.getSize(new THREE.Vector3());
          const maxDim = Math.max(size.x, size.y, size.z);
          controls.target.copy(center);
          camera.position.set(
            center.x + maxDim * 1.5,
            center.y + maxDim * 0.8,
            center.z + maxDim * 1.5
          );
          controls.update();
        }
      })
      .catch(console.error);

    // Animation
    let animId: number;
    const animate = () => {
      animId = requestAnimationFrame(animate);
      controls.update();
      renderer.render(scene, camera);
    };
    animate();

    // Resize
    const ro = new ResizeObserver(() => {
      const w = container.clientWidth;
      const h = container.clientHeight;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
    });
    ro.observe(container);

    return () => {
      cancelAnimationFrame(animId);
      ro.disconnect();
      renderer.dispose();
      container.innerHTML = "";
    };
  }, [filename]);

  return (
    <div
      ref={containerRef}
      style={{
        width: "100%",
        height: "100%",
        background: "#0f172a",
        position: "relative",
      }}
    >
      <div
        style={{
          position: "absolute",
          top: "50%",
          left: "50%",
          transform: "translate(-50%, -50%)",
          color: "#94a3b8",
          display: filename ? "none" : "block",
          textAlign: "center",
        }}
      >
        <div style={{ fontSize: "3rem", marginBottom: 8 }}>🏗️</div>
        <div>Сгенерируй или выбери модель</div>
      </div>
    </div>
  );
}
