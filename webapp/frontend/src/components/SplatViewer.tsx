/**
 * SplatViewer — просмотр Gaussian Splat (.ply) в браузере.
 * Использует @mkkellogg/gaussian-splats-3d поверх Three.js.
 * Если библиотека недоступна, отображает fallback-сообщение.
 */
import { useEffect, useRef, useState } from "react";

const API = "http://localhost:8765";

interface Props {
  jobId: string;
  filename: string;
}

export default function SplatViewer({ jobId, filename }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const viewerRef = useRef<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

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
          onProgress: (progress: number) => {
            if (progress >= 1) setLoading(false);
          },
        });

        viewer.start();
        setLoading(false);
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
  }, [jobId, filename]);

  return (
    <div style={{ position: "relative", width: "100%", height: "100%" }}>
      <div
        ref={containerRef}
        style={{ width: "100%", height: "100%", background: "#0f172a" }}
      />
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
