import { useState, useEffect, useRef, useCallback } from "react";
import SplatViewer from "../components/SplatViewer";

const API = "http://localhost:8765";

interface LlmAnalysis {
  extraction?: string;
  colmap?: string;
  report?: string;
  error_diagnosis?: string;
}

interface Job {
  id: string;
  project_name: string;
  status: "pending" | "extracting" | "colmap" | "training" | "done" | "error";
  step: string;
  progress: number;
  llm_analysis: LlmAnalysis;
  output_ply: string | null;
  created_at: string;
}

interface PlyModel {
  job_id: string;
  project_name: string;
  ply_filename: string;
  created_at: string;
  size_mb: number;
}

const STATUS_LABEL: Record<string, string> = {
  pending:    "⏳ Ожидание",
  extracting: "🎞️ Извлечение кадров",
  colmap:     "📐 COLMAP",
  training:   "🧠 Обучение",
  done:       "✅ Готово",
  error:      "❌ Ошибка",
};

const STATUS_COLOR: Record<string, string> = {
  pending:    "var(--text2)",
  extracting: "var(--accent)",
  colmap:     "#a78bfa",
  training:   "#f59e0b",
  done:       "var(--success)",
  error:      "var(--danger)",
};

export default function GsplatTab() {
  const [tab, setTab] = useState<"pipeline" | "viewer">("pipeline");

  // Upload state
  const [file, setFile] = useState<File | null>(null);
  const [projectName, setProjectName] = useState("");
  const [fps, setFps] = useState(1.0);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const dragRef = useRef<HTMLDivElement>(null);

  // Jobs state
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selectedJob, setSelectedJob] = useState<Job | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const logOffsetRef = useRef(0);
  const logEndRef = useRef<HTMLDivElement>(null);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // PLY upload state
  const [plyFile, setPlyFile] = useState<File | null>(null);
  const [plyProjectName, setPlyProjectName] = useState("");
  const [plyUploading, setPlyUploading] = useState(false);

  // Viewer state
  const [viewerJob, setViewerJob] = useState<{ jobId: string; filename: string } | null>(null);
  const [models, setModels] = useState<PlyModel[]>([]);

  // ── Fetch jobs list ──────────────────────────────────────
  const fetchJobs = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/gsplat/jobs`);
      const d = await r.json();
      setJobs(d.jobs || []);
    } catch {}
  }, []);

  // ── Fetch models (ready PLY files) ──────────────────────
  const fetchModels = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/gsplat/models`);
      const d = await r.json();
      setModels(d.models || []);
    } catch {}
  }, []);

  useEffect(() => {
    fetchJobs();
    fetchModels();
  }, [fetchJobs, fetchModels]);

  // ── Poll active job logs ─────────────────────────────────
  useEffect(() => {
    if (!selectedJob) return;
    logOffsetRef.current = 0;
    setLogs([]);

    const poll = async () => {
      try {
        const r = await fetch(
          `${API}/api/gsplat/jobs/${selectedJob.id}?log_offset=${logOffsetRef.current}`
        );
        const d = await r.json();
        if (d.logs?.length) {
          setLogs(prev => [...prev, ...d.logs]);
          logOffsetRef.current += d.logs.length;
        }
        // Update job in list
        setJobs(prev => prev.map(j => j.id === d.id ? { ...j, ...d } : j));
        setSelectedJob(prev => prev?.id === d.id ? { ...prev, ...d } : prev);

        if (d.status === "done" || d.status === "error") {
          fetchModels();
        }
      } catch {}
    };

    poll();
    pollingRef.current = setInterval(poll, 2000);
    return () => {
      if (pollingRef.current) clearInterval(pollingRef.current);
    };
  }, [selectedJob?.id, fetchModels]);

  // Auto-scroll logs
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  // ── Drag & drop ──────────────────────────────────────────
  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    const f = e.dataTransfer.files[0];
    if (f) { setFile(f); setProjectName(f.name.replace(/\.[^.]+$/, "")); }
  };

  // ── Upload video ─────────────────────────────────────────
  const handleUpload = async () => {
    if (!file) return;
    setUploading(true);
    setUploadError("");
    const fd = new FormData();
    fd.append("file", file);
    fd.append("project_name", projectName || file.name);
    fd.append("fps", String(fps));
    try {
      const r = await fetch(`${API}/api/gsplat/upload`, { method: "POST", body: fd });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "Ошибка загрузки");
      setFile(null);
      setProjectName("");
      await fetchJobs();
      // Select new job
      const jobs2 = await fetch(`${API}/api/gsplat/jobs`).then(r => r.json());
      const newJob = jobs2.jobs?.find((j: Job) => j.id === d.job_id);
      if (newJob) setSelectedJob(newJob);
    } catch (e: any) {
      setUploadError(e.message);
    } finally {
      setUploading(false);
    }
  };

  // ── Upload PLY ───────────────────────────────────────────
  const handlePlyUpload = async () => {
    if (!plyFile) return;
    setPlyUploading(true);
    const fd = new FormData();
    fd.append("file", plyFile);
    fd.append("project_name", plyProjectName || plyFile.name);
    try {
      const r = await fetch(`${API}/api/gsplat/upload-ply`, { method: "POST", body: fd });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail);
      setPlyFile(null);
      setPlyProjectName("");
      await fetchJobs();
      await fetchModels();
    } catch (e: any) {
      alert(e.message);
    } finally {
      setPlyUploading(false);
    }
  };

  // ── Open in viewer ───────────────────────────────────────
  const openInViewer = (m: PlyModel) => {
    setViewerJob({ jobId: m.job_id, filename: m.ply_filename });
    setTab("viewer");
  };

  // ── Render ───────────────────────────────────────────────
  return (
    <div>
      <h2 style={{ marginBottom: 16, display: "flex", alignItems: "center", gap: 10 }}>
        🗺️ 3D-карты (Gaussian Splatting)
        <span style={{ fontSize: "0.75rem", color: "var(--text2)", fontWeight: 400 }}>
          видео / фото → COLMAP → gsplat → .ply
        </span>
      </h2>

      {/* Sub-tabs */}
      <div style={{ display: "flex", gap: 8, marginBottom: 20, borderBottom: "2px solid var(--border)", paddingBottom: 8 }}>
        {(["pipeline", "viewer"] as const).map(t => (
          <button key={t} onClick={() => setTab(t)} style={{
            padding: "6px 16px", borderRadius: 8, border: "none", cursor: "pointer",
            background: tab === t ? "var(--accent)" : "var(--surface2)",
            color: tab === t ? "#0f172a" : "var(--text)",
            fontWeight: tab === t ? 600 : 400,
          }}>
            {t === "pipeline" ? "🔧 Пайплайн" : "👁️ Просмотр"}
          </button>
        ))}
      </div>

      {/* ══════════ PIPELINE TAB ══════════ */}
      {tab === "pipeline" && (
        <div style={{ display: "grid", gridTemplateColumns: "340px 1fr", gap: 20 }}>

          {/* Left: upload + jobs list */}
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>

            {/* Upload video */}
            <div style={{ background: "var(--surface)", borderRadius: 12, padding: 16 }}>
              <h3 style={{ marginBottom: 12, fontSize: "0.9rem", color: "var(--accent)" }}>
                📹 Загрузить видео
              </h3>

              {/* Drag zone */}
              <div
                ref={dragRef}
                onDrop={handleDrop}
                onDragOver={e => e.preventDefault()}
                onClick={() => document.getElementById("video-input")?.click()}
                style={{
                  border: "2px dashed var(--border)", borderRadius: 10,
                  padding: "24px 16px", textAlign: "center", cursor: "pointer",
                  color: "var(--text2)", marginBottom: 12,
                  background: file ? "rgba(56,189,248,0.05)" : "transparent",
                  transition: "background 0.2s",
                }}
              >
                <div style={{ fontSize: "2rem", marginBottom: 6 }}>🎥</div>
                {file
                  ? <><strong style={{ color: "var(--text)" }}>{file.name}</strong><br />
                    <span style={{ fontSize: "0.75rem" }}>{(file.size / 1024 / 1024).toFixed(1)} МБ</span></>
                  : <span>Перетащи видео сюда или нажми<br />
                    <span style={{ fontSize: "0.75rem" }}>mp4 · mov · avi · mkv</span></span>
                }
                <input
                  id="video-input" type="file"
                  accept="video/*" style={{ display: "none" }}
                  onChange={e => {
                    const f = e.target.files?.[0];
                    if (f) { setFile(f); setProjectName(f.name.replace(/\.[^.]+$/, "")); }
                  }}
                />
              </div>

              {file && (
                <>
                  <input
                    value={projectName}
                    onChange={e => setProjectName(e.target.value)}
                    placeholder="Название проекта"
                    style={{
                      width: "100%", padding: "7px 10px", borderRadius: 8,
                      background: "var(--surface2)", border: "1px solid var(--border)",
                      color: "var(--text)", marginBottom: 10,
                    }}
                  />
                  <label style={{ fontSize: "0.8rem", color: "var(--text2)", display: "block", marginBottom: 6 }}>
                    Кадров в секунду: <strong style={{ color: "var(--text)" }}>{fps}</strong>
                    <span style={{ fontSize: "0.7rem", marginLeft: 6 }}>
                      (≈{Math.round(fps * 60)} кадров/мин)
                    </span>
                  </label>
                  <input type="range" min={0.2} max={5} step={0.1}
                    value={fps} onChange={e => setFps(Number(e.target.value))}
                    style={{ width: "100%", marginBottom: 12, accentColor: "var(--accent)" }}
                  />
                  {uploadError && (
                    <div style={{ color: "var(--danger)", fontSize: "0.8rem", marginBottom: 8 }}>
                      {uploadError}
                    </div>
                  )}
                  <button onClick={handleUpload} disabled={uploading} style={{
                    width: "100%", padding: "9px 0", borderRadius: 8, border: "none",
                    background: "var(--accent)", color: "#0f172a", fontWeight: 600, cursor: "pointer",
                  }}>
                    {uploading ? "⏳ Загружаю..." : "🚀 Запустить пайплайн"}
                  </button>
                </>
              )}
            </div>

            {/* Upload PLY directly */}
            <div style={{ background: "var(--surface)", borderRadius: 12, padding: 16 }}>
              <h3 style={{ marginBottom: 10, fontSize: "0.85rem", color: "var(--text2)" }}>
                📦 Загрузить готовый .ply
              </h3>
              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                <label style={{
                  padding: "6px 12px", background: "var(--surface2)", borderRadius: 8,
                  cursor: "pointer", fontSize: "0.8rem", color: "var(--text)",
                }}>
                  {plyFile ? plyFile.name : "Выбрать .ply"}
                  <input type="file" accept=".ply" style={{ display: "none" }}
                    onChange={e => { const f = e.target.files?.[0]; if (f) { setPlyFile(f); setPlyProjectName(f.name.replace(".ply", "")); } }}
                  />
                </label>
                {plyFile && (
                  <>
                    <input value={plyProjectName} onChange={e => setPlyProjectName(e.target.value)}
                      placeholder="Название" style={{
                        flex: 1, padding: "6px 10px", borderRadius: 8,
                        background: "var(--surface2)", border: "1px solid var(--border)", color: "var(--text)",
                        fontSize: "0.8rem",
                      }}
                    />
                    <button onClick={handlePlyUpload} disabled={plyUploading} style={{
                      padding: "6px 14px", borderRadius: 8, border: "none",
                      background: "var(--accent2)", color: "#fff", cursor: "pointer", fontSize: "0.8rem",
                    }}>
                      {plyUploading ? "..." : "↑"}
                    </button>
                  </>
                )}
              </div>
            </div>

            {/* Jobs list */}
            <div style={{ background: "var(--surface)", borderRadius: 12, padding: 16 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
                <h3 style={{ fontSize: "0.9rem", color: "var(--accent)" }}>📋 Задачи</h3>
                <button onClick={fetchJobs} style={{
                  fontSize: "0.75rem", padding: "3px 8px", borderRadius: 6,
                  background: "var(--surface2)", border: "none", color: "var(--text2)", cursor: "pointer",
                }}>↻</button>
              </div>
              {jobs.length === 0
                ? <div style={{ color: "var(--text2)", fontSize: "0.8rem" }}>Нет задач</div>
                : jobs.map(job => (
                  <div key={job.id}
                    onClick={() => setSelectedJob(job)}
                    style={{
                      padding: "10px 12px", borderRadius: 8, marginBottom: 8, cursor: "pointer",
                      background: selectedJob?.id === job.id ? "var(--surface2)" : "transparent",
                      border: `1px solid ${selectedJob?.id === job.id ? "var(--accent)" : "var(--border)"}`,
                    }}>
                    <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                      <strong style={{ fontSize: "0.85rem" }}>{job.project_name}</strong>
                      <span style={{ fontSize: "0.75rem", color: STATUS_COLOR[job.status] }}>
                        {STATUS_LABEL[job.status] || job.status}
                      </span>
                    </div>
                    {/* Progress bar */}
                    <div style={{ background: "var(--surface2)", borderRadius: 4, height: 4, overflow: "hidden" }}>
                      <div style={{
                        height: "100%", borderRadius: 4,
                        width: `${job.progress}%`,
                        background: job.status === "error" ? "var(--danger)" : "var(--accent)",
                        transition: "width 0.5s",
                      }} />
                    </div>
                    <div style={{ fontSize: "0.7rem", color: "var(--text2)", marginTop: 4 }}>
                      {job.step}
                    </div>
                    {/* Open in viewer if done */}
                    {job.status === "done" && job.output_ply && (
                      <button
                        onClick={e => { e.stopPropagation(); const m = models.find(m => m.job_id === job.id); if (m) openInViewer(m); }}
                        style={{
                          marginTop: 8, padding: "4px 10px", borderRadius: 6, border: "none",
                          background: "var(--success)", color: "#fff", fontSize: "0.75rem", cursor: "pointer",
                        }}>
                        👁️ Открыть вьюер
                      </button>
                    )}
                  </div>
                ))
              }
            </div>
          </div>

          {/* Right: logs + LLM analysis */}
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            {selectedJob ? (
              <>
                <div style={{ background: "var(--surface)", borderRadius: 12, padding: 16 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
                    <h3 style={{ fontSize: "0.9rem" }}>
                      📜 Логи: <span style={{ color: "var(--accent)" }}>{selectedJob.project_name}</span>
                    </h3>
                    <span style={{ fontSize: "0.8rem", color: STATUS_COLOR[selectedJob.status] }}>
                      {STATUS_LABEL[selectedJob.status]} · {selectedJob.progress}%
                    </span>
                  </div>
                  <div style={{
                    background: "#0a0f1a", borderRadius: 8, padding: 12, height: 320,
                    overflowY: "auto", fontFamily: "monospace", fontSize: "0.72rem",
                    color: "#94a3b8", lineHeight: 1.6,
                  }}>
                    {logs.length === 0
                      ? <span style={{ color: "var(--text2)" }}>Ожидание логов...</span>
                      : logs.map((line, i) => (
                        <div key={i} style={{
                          color: line.startsWith("[LLM]") ? "#a78bfa"
                            : line.startsWith("❌") ? "#ef4444"
                            : line.startsWith("✅") ? "#22c55e"
                            : line.startsWith("$") ? "#38bdf8"
                            : line.startsWith("=") ? "#f59e0b"
                            : "#94a3b8",
                        }}>
                          {line || " "}
                        </div>
                      ))
                    }
                    <div ref={logEndRef} />
                  </div>
                </div>

                {/* LLM Analysis cards */}
                {Object.keys(selectedJob.llm_analysis).length > 0 && (
                  <div style={{ background: "var(--surface)", borderRadius: 12, padding: 16 }}>
                    <h3 style={{ fontSize: "0.9rem", marginBottom: 12, color: "#a78bfa" }}>
                      🧠 Анализ ИИ-оркестратора
                    </h3>
                    {selectedJob.llm_analysis.extraction && (
                      <LlmCard title="📹 Входные данные" text={selectedJob.llm_analysis.extraction} />
                    )}
                    {selectedJob.llm_analysis.colmap && (
                      <LlmCard title="📐 COLMAP" text={selectedJob.llm_analysis.colmap} />
                    )}
                    {selectedJob.llm_analysis.report && (
                      <LlmCard title="✅ Финальный отчёт" text={selectedJob.llm_analysis.report} color="#22c55e" />
                    )}
                    {selectedJob.llm_analysis.error_diagnosis && (
                      <LlmCard title="🔍 Диагностика ошибки" text={selectedJob.llm_analysis.error_diagnosis} color="#ef4444" />
                    )}
                  </div>
                )}
              </>
            ) : (
              <div style={{
                background: "var(--surface)", borderRadius: 12, padding: 32,
                display: "flex", flexDirection: "column", alignItems: "center",
                gap: 12, color: "var(--text2)",
              }}>
                <div style={{ fontSize: "3rem" }}>🗺️</div>
                <div style={{ textAlign: "center" }}>
                  <strong style={{ color: "var(--text)" }}>Как это работает</strong>
                  <div style={{ fontSize: "0.85rem", marginTop: 8, lineHeight: 1.8 }}>
                    1. Загрузи видео с регистратора или фотографии<br />
                    2. LLM анализирует данные и задаёт параметры<br />
                    3. COLMAP восстанавливает позиции камер<br />
                    4. gsplat обучает 3D-модель сцены (~10–20 мин)<br />
                    5. Открой результат в 3D-вьюере
                  </div>
                  <div style={{ marginTop: 12, fontSize: "0.75rem", color: "var(--text2)", padding: "10px 16px", background: "var(--surface2)", borderRadius: 8 }}>
                    ⚠️ Требуется: <strong>ffmpeg</strong>, <strong>COLMAP</strong>,<br />
                    <strong>Nerfstudio</strong> или <strong>gsplat</strong> + NVIDIA GPU
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ══════════ VIEWER TAB ══════════ */}
      {tab === "viewer" && (
        <div style={{ display: "grid", gridTemplateColumns: "260px 1fr", gap: 20, height: "calc(100vh - 200px)" }}>

          {/* Model list */}
          <div style={{ background: "var(--surface)", borderRadius: 12, padding: 16, overflowY: "auto" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
              <h3 style={{ fontSize: "0.9rem", color: "var(--accent)" }}>📦 Модели</h3>
              <button onClick={fetchModels} style={{
                fontSize: "0.75rem", padding: "3px 8px", borderRadius: 6,
                background: "var(--surface2)", border: "none", color: "var(--text2)", cursor: "pointer",
              }}>↻</button>
            </div>
            {models.length === 0
              ? <div style={{ color: "var(--text2)", fontSize: "0.8rem" }}>
                  Нет готовых моделей.<br />
                  Запусти пайплайн или загрузи .ply файл.
                </div>
              : models.map(m => (
                <div key={m.job_id}
                  onClick={() => setViewerJob({ jobId: m.job_id, filename: m.ply_filename })}
                  style={{
                    padding: "10px 12px", borderRadius: 8, marginBottom: 8, cursor: "pointer",
                    background: viewerJob?.jobId === m.job_id ? "var(--surface2)" : "transparent",
                    border: `1px solid ${viewerJob?.jobId === m.job_id ? "var(--accent)" : "var(--border)"}`,
                  }}>
                  <div style={{ fontWeight: 600, fontSize: "0.85rem", marginBottom: 2 }}>{m.project_name}</div>
                  <div style={{ fontSize: "0.72rem", color: "var(--text2)" }}>
                    {m.ply_filename}<br />{m.size_mb} МБ
                  </div>
                </div>
              ))
            }
          </div>

          {/* 3D Viewer */}
          <div style={{ background: "var(--surface)", borderRadius: 12, overflow: "hidden" }}>
            {viewerJob
              ? <SplatViewer jobId={viewerJob.jobId} filename={viewerJob.filename} />
              : (
                <div style={{
                  height: "100%", display: "flex", flexDirection: "column",
                  alignItems: "center", justifyContent: "center", gap: 12, color: "var(--text2)",
                }}>
                  <div style={{ fontSize: "3rem" }}>🌐</div>
                  <div>Выбери модель из списка слева</div>
                </div>
              )
            }
          </div>
        </div>
      )}
    </div>
  );
}

function LlmCard({ title, text, color = "var(--accent2)" }: { title: string; text: string; color?: string }) {
  return (
    <div style={{
      background: "var(--surface2)", borderRadius: 8, padding: 12,
      marginBottom: 10, borderLeft: `3px solid ${color}`,
    }}>
      <div style={{ fontSize: "0.78rem", fontWeight: 600, color, marginBottom: 6 }}>{title}</div>
      <div style={{ fontSize: "0.82rem", color: "var(--text)", lineHeight: 1.6, whiteSpace: "pre-wrap" }}>
        {text}
      </div>
    </div>
  );
}
