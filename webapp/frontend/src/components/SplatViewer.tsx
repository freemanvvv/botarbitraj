/**
 * SplatViewer — просмотр Gaussian Splat (.ply) в браузере.
 * Использует @mkkellogg/gaussian-splats-3d поверх Three.js.
 * Если библиотека недоступна, отображает fallback-сообщение.
 */
import { useEffect, useRef, useState } from "react";

// В проде фронт отдаётся тем же бэкендом → относительный base (origin
// страницы), иначе localhost↔127.0.0.1 = разные origin и CORS роняет
// запросы («Failed to fetch»). В dev (vite :5173) — абсолютный адрес.
const API = import.meta.env.DEV ? "http://localhost:8765" : "";

interface Props {
  jobId: string;
  filename: string;
}

export default function SplatViewer({ jobId, filename }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const viewerRef = useRef<any>(null);
  const initRef = useRef<any>(null);   // стартовые камера+цель для «сброса»
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  // COLMAP не задаёт «верх» сцены, и splat из него обычно грузится вверх ногами
  // → по умолчанию поворачиваем на 180° вокруг оси X (кватернион [1,0,0,0]).
  // Кнопка «Перевернуть» переключает на случай, если конкретная сцена вышла
  // наоборот.
  const [flipped, setFlipped] = useState(true);

  useEffect(() => {
    if (!containerRef.current || !jobId || !filename) return;
    const container = containerRef.current;
    setLoading(true);
    setError(null);

    let destroyed = false;

    (async () => {
      try {
        // Динамический импорт — не ломает сборку если пакет отсутствует
        const GS = await import("@mkkellogg/gaussian-splats-3d").catch(() => null);

        if (!GS) {
          setError(
            "Библиотека @mkkellogg/gaussian-splats-3d не установлена.\n" +
            "Выполни: npm install @mkkellogg/gaussian-splats-3d"
          );
          setLoading(false);
          return;
        }

        if (destroyed) return;

        const plyUrl = `${API}/api/gsplat/ply/${jobId}/${filename}`;

        const viewer = new GS.Viewer({
          rootElement: container,
          selfDrivenMode: true,
          useBuiltInControls: true,
          sharedMemoryForWorkers: false,
        });

        viewerRef.current = viewer;

        await viewer.addSplatScene(plyUrl, {
          progressiveLoad: true,
          // кватернион поворота сцены: [1,0,0,0] = 180° вокруг X (ставит
          // «вверх ногами» COLMAP-сцену правильно), [0,0,0,1] = без поворота.
          rotation: flipped ? [1, 0, 0, 0] : [0, 0, 0, 1],
          onProgress: (progress: number) => {
            if (progress >= 1) setLoading(false);
          },
        });

        viewer.start();
        setLoading(false);

        // Запоминаем стартовый вид и делаем управление мышью поживее.
        const cam = viewer.camera, ctr = viewer.controls;
        if (cam && ctr) {
          initRef.current = {
            px: cam.position.x, py: cam.position.y, pz: cam.position.z,
            tx: ctr.target.x, ty: ctr.target.y, tz: ctr.target.z,
          };
          try {
            ctr.enablePan = true; ctr.enableZoom = true; ctr.enableRotate = true;
            ctr.zoomSpeed = 1.5; ctr.panSpeed = 1.2; ctr.rotateSpeed = 0.9;
          } catch { /* контролы иной реализации — необязательные настройки */ }
        }
      } catch (e: any) {
        if (!destroyed) {
          setError(`Ошибка загрузки: ${e?.message || e}`);
          setLoading(false);
        }
      }
    })();

    return () => {
      destroyed = true;
      if (viewerRef.current) {
        try { viewerRef.current.dispose?.(); } catch {}
        viewerRef.current = null;
      }
      container.innerHTML = "";
    };
  }, [jobId, filename, flipped]);

  // ── Управление камерой (кнопками, а не только мышью) ────────
  // Работаем напрямую с камерой и OrbitControls вьюера: зум = движение вдоль
  // луча к точке вращения, панорама = сдвиг камеры и точки по базису вида.
  const cc = () => {
    const v = viewerRef.current;
    const cam = v?.camera, ctr = v?.controls;
    return cam && ctr ? { cam, ctr } : null;
  };

  const zoomBy = (factor: number) => {
    const g = cc(); if (!g) return;
    const { cam, ctr } = g, t = ctr.target;
    cam.position.x = t.x + (cam.position.x - t.x) * factor;
    cam.position.y = t.y + (cam.position.y - t.y) * factor;
    cam.position.z = t.z + (cam.position.z - t.z) * factor;
    ctr.update?.();
  };

  const pan = (dx: number, dy: number) => {
    const g = cc(); if (!g) return;
    const { cam, ctr } = g;
    cam.updateMatrixWorld?.();
    const e = cam.matrixWorld.elements;               // столбцы = базис камеры
    const dist = Math.hypot(cam.position.x - ctr.target.x, cam.position.y - ctr.target.y, cam.position.z - ctr.target.z) || 1;
    const s = 0.12 * dist;
    const ox = (e[0] * dx + e[4] * dy) * s;
    const oy = (e[1] * dx + e[5] * dy) * s;
    const oz = (e[2] * dx + e[6] * dy) * s;
    cam.position.x += ox; cam.position.y += oy; cam.position.z += oz;
    ctr.target.x += ox; ctr.target.y += oy; ctr.target.z += oz;
    ctr.update?.();
  };

  const resetView = () => {
    const g = cc(); if (!g || !initRef.current) return;
    const { cam, ctr } = g, i = initRef.current;
    cam.position.set(i.px, i.py, i.pz);
    ctr.target.set(i.tx, i.ty, i.tz);
    ctr.update?.();
  };

  const btn: React.CSSProperties = {
    width: 34, height: 34, borderRadius: 8, cursor: "pointer",
    border: "1px solid var(--border2)", background: "rgba(15,23,42,0.9)",
    color: "#fff", fontSize: "1rem", fontFamily: "inherit",
    display: "flex", alignItems: "center", justifyContent: "center", lineHeight: 1,
  };

  return (
    <div style={{ position: "relative", width: "100%", height: "100%" }}>
      <div
        ref={containerRef}
        style={{ width: "100%", height: "100%", background: "#0f172a" }}
      />

      {!error && !loading && (
        <>
          {/* Панель управления: зум + панорама + сброс */}
          <div style={{
            position: "absolute", bottom: 44, right: 12, zIndex: 6,
            display: "flex", flexDirection: "column", gap: 6, alignItems: "center",
          }}>
            <button style={btn} title="Приблизить" onClick={() => zoomBy(0.8)}>➕</button>
            <button style={btn} title="Отдалить" onClick={() => zoomBy(1.25)}>➖</button>
            <div style={{ height: 4 }} />
            <button style={btn} title="Вверх" onClick={() => pan(0, 1)}>▲</button>
            <div style={{ display: "flex", gap: 6 }}>
              <button style={btn} title="Влево" onClick={() => pan(-1, 0)}>◀</button>
              <button style={btn} title="Сбросить вид" onClick={resetView}>⟲</button>
              <button style={btn} title="Вправо" onClick={() => pan(1, 0)}>▶</button>
            </div>
            <button style={btn} title="Вниз" onClick={() => pan(0, -1)}>▼</button>
          </div>

          {/* Подсказка по мыши/трекпаду */}
          <div style={{
            position: "absolute", bottom: 10, left: 12, zIndex: 6,
            fontSize: "0.7rem", color: "rgba(255,255,255,0.75)",
            background: "rgba(15,23,42,0.7)", padding: "4px 8px", borderRadius: 6,
          }}>
            ЛКМ — вращать · колесо — зум · ПКМ / 2 пальца — двигать
          </div>
        </>
      )}
      {!error && (
        <button
          onClick={() => setFlipped(f => !f)}
          title="Перевернуть сцену (COLMAP не задаёт «верх»)"
          style={{
            position: "absolute", top: 10, right: 10, zIndex: 6,
            padding: "6px 12px", borderRadius: 8, cursor: "pointer",
            border: "1px solid var(--border2)", background: "rgba(15,23,42,0.85)",
            color: "#fff", fontFamily: "inherit", fontSize: "0.8rem",
          }}
        >
          🔄 Перевернуть
        </button>
      )}
      {loading && (
        <div style={{
          position: "absolute", inset: 0, display: "flex",
          alignItems: "center", justifyContent: "center",
          background: "rgba(15,23,42,0.85)", flexDirection: "column", gap: 12,
        }}>
          <div style={{ fontSize: "2rem" }}>⏳</div>
          <div style={{ color: "var(--text2)" }}>Загрузка Gaussian Splat...</div>
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
