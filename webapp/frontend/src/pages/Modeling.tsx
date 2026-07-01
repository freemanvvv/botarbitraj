import { useState, useEffect, useRef } from "react";
import ThreeViewer from "../components/ThreeViewer";

const API = "http://localhost:8765";

interface Stats {
  walls: number; slabs: number; windows: number;
  doors: number; columns: number; openings: number; storeys: number;
}

interface IFCFile {
  name: string; size_kb: number; created: number; url: string;
}

const DEFAULT_PARAMS = {
  name: "Building",
  length: 15, width: 12, height: 6,
  num_floors: 2,
  wall_thickness: 0.4, slab_thickness: 0.2,
  roof_type: "gable",
  add_internal_walls: true,
  add_windows: true, add_doors: true,
  add_columns: true, add_balconies: false, add_foundation: true,
  windows_per_wall_long: 3, windows_per_wall_short: 2,
  window_width: 1.2, window_height: 1.5, window_sill: 0.9,
  door_width: 0.9, door_height: 2.1,
};

type Tab = "create" | "plan" | "nl" | "arch" | "view";

interface ArchReasoning {
  footprint: string; floors: string; facade: string; structure: string; layout: string;
}

interface ArchPlan {
  total_area_m2?: number; persons?: number; area_per_person?: number; norm_ref_area?: string;
  floor_count?: number; floor_height_m?: number; norm_ref_height?: string;
  wall_material?: string; wall_thickness_m?: number; norm_ref_wall?: string;
  slab_thickness_m?: number; norm_ref_slab?: string;
  window_sill_m?: number; norm_ref_sill?: string;
  lintel_height_m?: number; norm_ref_lintel?: string;
  foundation_depth_m?: number; norm_ref_foundation?: string;
}

interface ArchStage {
  stage: string; description: string; norm_refs?: string[];
}

interface ArchBuilding {
  entrances?: number; apartments_per_landing?: number;
  has_elevator?: boolean; elevators_per_entrance?: number;
  elevator_capacity_kg?: number; elevator_shaft_m?: string;
  stair_width_m?: number; riser_shaft_m?: string; electrical_niche_m?: string;
}

interface IntegrityIssue {
  severity: "error" | "warning" | "info";
  element_type: string;
  element_name: string;
  message: string;
}

interface IntegrityResult {
  ok: boolean;
  issues: IntegrityIssue[];
  summary: string;
  counts: { errors: number; warnings: number; total_elements: number };
}

export default function Modeling() {
  const [params, setParams] = useState({ ...DEFAULT_PARAMS });
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(false);
  const [nlLoading, setNlLoading] = useState(false);
  const [imgLoading, setImgLoading] = useState(false);
  const [archLoading, setArchLoading] = useState(false);
  const [files, setFiles] = useState<IFCFile[]>([]);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("create");
  const [description, setDescription] = useState("");
  const [planFile, setPlanFile] = useState<File | null>(null);
  const [planDesc, setPlanDesc] = useState("");
  const [analysisNotes, setAnalysisNotes] = useState("");
  const [analysisError, setAnalysisError] = useState("");
  const [archReq, setArchReq] = useState("");
  const [archName, setArchName] = useState("");
  const [archSummary, setArchSummary] = useState("");
  const [archNormStudy, setArchNormStudy] = useState("");
  const [archPlan, setArchPlan] = useState<ArchPlan | null>(null);
  const [archViolations, setArchViolations] = useState<string[]>([]);
  const [archReasoning, setArchReasoning] = useState<ArchReasoning | null>(null);
  const [archStages, setArchStages] = useState<ArchStage[]>([]);
  const [archBuilding, setArchBuilding] = useState<ArchBuilding | null>(null);
  const [archStep, setArchStep] = useState<"idle"|"norms"|"planning"|"generating"|"done">("idle");
  const [archError, setArchError] = useState("");
  const [archModels, setArchModels] = useState<string[]>(["local-model"]);
  const [archModel, setArchModel] = useState("local-model");
  const [archIntegrity, setArchIntegrity] = useState<IntegrityResult | null>(null);
  const [archFloorplanMode, setArchFloorplanMode] = useState<"solver" | "neural">("solver");
  const planInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    fetch(`${API}/api/model/existing`)
      .then(r => r.json())
      .then(d => { setFiles(d.files); if (d.files.length) setSelectedFile(d.files[0].name); })
      .catch(() => {});
    fetch(`${API}/api/chat/models`)
      .then(r => r.json())
      .then(d => { if (d.models?.length) { setArchModels(d.models); setArchModel(d.models[0]); } })
      .catch(() => {});
  }, []);

  const up = (key: string, val: any) => setParams(p => ({ ...p, [key]: val }));

  const refreshFiles = async () => {
    const r = await fetch(`${API}/api/model/existing`);
    setFiles((await r.json()).files);
  };

  const generate = async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API}/api/model/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...params, height: params.height }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Ошибка");
      setStats(data.stats);
      setSelectedFile(data.filename);
      await refreshFiles();
      setTab("view");
    } catch (e: any) { alert(e.message); }
    finally { setLoading(false); }
  };

  const analyzeImage = async () => {
    setImgLoading(true);
    setAnalysisError("");
    setAnalysisNotes("");
    try {
      const fd = new FormData();
      if (planFile) fd.append("file", planFile);
      fd.append("description", planDesc);
      const res = await fetch(`${API}/api/model/analyze-image`, { method: "POST", body: fd });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Ошибка LLM");
      const p = data.params;
      setParams(prev => ({
        ...prev,
        name: p.name || prev.name,
        length: p.length || prev.length,
        width: p.width || prev.width,
        height: p.height || prev.height,
        num_floors: p.num_floors || prev.num_floors,
        roof_type: p.roof_type || prev.roof_type,
        windows_per_wall_long: p.windows_per_wall_long ?? prev.windows_per_wall_long,
        windows_per_wall_short: p.windows_per_wall_short ?? prev.windows_per_wall_short,
        window_width: p.window_width || prev.window_width,
        window_height: p.window_height || prev.window_height,
        window_sill: p.window_sill || prev.window_sill,
        door_width: p.door_width || prev.door_width,
        door_height: p.door_height || prev.door_height,
        add_columns: p.add_columns ?? prev.add_columns,
        add_balconies: p.add_balconies ?? prev.add_balconies,
      }));
      setAnalysisNotes(p.notes || p.description || "Параметры извлечены");
      setTab("create");
    } catch (e: any) { setAnalysisError(e.message); }
    finally { setImgLoading(false); }
  };

  const generateFromDesc = async () => {
    if (!description.trim()) return;
    setNlLoading(true);
    try {
      const res = await fetch(`${API}/api/model/bim-generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ description }),
      });
      const d = await res.json();
      if (!res.ok) throw new Error(d.detail || "Ошибка");
      setStats(d.stats); setSelectedFile(d.filename);
      await refreshFiles(); setTab("view");
    } catch (e: any) { alert("❌ " + e.message); }
    finally { setNlLoading(false); }
  };

  const designWithAI = async () => {
    if (!archReq.trim()) return;
    setArchLoading(true);
    setArchError("");
    setArchReasoning(null);
    setArchPlan(null);
    setArchNormStudy("");
    setArchViolations([]);
    setArchIntegrity(null);
    setArchName(""); setArchSummary("");
    setArchStep("norms");
    try {
      // Simulate step progression visually
      const stepTimer = setTimeout(() => setArchStep("planning"), 4000);
      const stepTimer2 = setTimeout(() => setArchStep("generating"), 9000);
      const res = await fetch(`${API}/api/model/architect`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ requirements: archReq, model: archModel, floorplan_mode: archFloorplanMode }),
      });
      clearTimeout(stepTimer); clearTimeout(stepTimer2);
      const d = await res.json();
      if (!res.ok) throw new Error(d.detail || "Ошибка архитектора");
      setArchStep("done");
      setArchName(d.name || "");
      setArchSummary(d.summary || "");
      setArchNormStudy(d.norm_study || "");
      setArchPlan(d.plan || null);
      setArchReasoning(d.reasoning || null);
      setArchViolations(d.norm_violations_fixed || []);
      setArchStages(d.stages || []);
      setArchBuilding(d.building || null);
      setArchIntegrity(d.integrity || null);
      setStats(d.stats);
      setSelectedFile(d.filename);
      setParams(prev => ({ ...prev, ...d.params }));
      await refreshFiles();
    } catch (e: any) { setArchError(e.message); setArchStep("idle"); }
    finally { setArchLoading(false); }
  };

  const Label = ({ children }: { children: React.ReactNode }) => (
    <label style={{ display: "block", marginBottom: 13, fontSize: "0.78rem", color: "var(--text2)", fontWeight: 500, textTransform: "uppercase", letterSpacing: "0.06em" }}>
      {children}
    </label>
  );

  const Divider = ({ label }: { label: string }) => (
    <div style={{ display: "flex", alignItems: "center", gap: 8, margin: "14px 0 10px", fontSize: "0.68rem", color: "var(--text3)", textTransform: "uppercase", letterSpacing: "0.08em" }}>
      <div style={{ flex: 1, height: 1, background: "var(--border)" }} />
      {label}
      <div style={{ flex: 1, height: 1, background: "var(--border)" }} />
    </div>
  );

  const CheckRow = ({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) => (
    <label style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10, fontSize: "0.82rem", color: "var(--text)", cursor: "pointer", fontWeight: 400 }}>
      <input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)} style={{ accentColor: "var(--accent)", width: 15, height: 15 }} />
      {label}
    </label>
  );

  return (
    <div style={{ height: "calc(70vh + 40px)", display: "flex", flexDirection: "column" }}>
      <div style={{ marginBottom: 14, display: "flex", alignItems: "center", gap: 10 }}>
        <h2>Моделирование</h2>
        <div className="toggle-group">
          {([
            ["arch",   "🏛 Архитектор"],
            ["create", "Параметры"],
            ["plan",   "📐 По плану"],
            ["nl",     "🤖 Быстрый эскиз"],
            ["view",   "Просмотр"],
          ] as [Tab, string][]).map(([k, l]) => (
            <button key={k} className={`toggle-btn ${tab === k ? "active" : ""}`} onClick={() => setTab(k)}>{l}</button>
          ))}
        </div>
      </div>

      <div className="modeling-grid" style={{ flex: 1 }}>
        {/* ─── Left panel ─── */}
        <div className="modeling-params">

          {/* ══ AI АРХИТЕКТОР ══ */}
          {tab === "arch" && (<>
            <h3 style={{ marginBottom: 6, color: "var(--accent)" }}>🏛 AI Архитектор</h3>
            <p style={{ fontSize: "0.78rem", color: "var(--text2)", marginBottom: 4, lineHeight: 1.6 }}>
              Опиши требования — LLM изучит нормы КМК/ШНК, составит план по этапам строительства с обоснованием,
              проверит здание по нормам и целостности, затем сгенерирует IFC.
            </p>
            <p style={{ fontSize: "0.72rem", color: "var(--text3)", marginBottom: 10 }}>
              Дольше «Быстрого эскиза», но результат соответствует нормам — для реального проекта.
            </p>

            <Label>Локальная модель ИИ</Label>
            <select
              value={archModel}
              onChange={e => setArchModel(e.target.value)}
              disabled={archLoading}
              style={{ width: "100%", padding: "8px 10px", marginBottom: 10, background: "var(--bg2)", border: "1px solid var(--border)", borderRadius: 8, color: "var(--text)", fontSize: "0.83rem", fontFamily: "inherit" }}
            >
              {archModels.map(m => <option key={m} value={m}>{m}</option>)}
            </select>

            <label style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10, fontSize: "0.8rem", color: "var(--text)", cursor: archLoading ? "default" : "pointer" }}>
              <input
                type="checkbox"
                checked={archFloorplanMode === "neural"}
                onChange={e => setArchFloorplanMode(e.target.checked ? "neural" : "solver")}
                disabled={archLoading}
                style={{ accentColor: "var(--accent)", width: 15, height: 15 }}
              />
              🧠 ИИ-планировка квартир (экспериментально)
            </label>
            {archFloorplanMode === "neural" && (
              <p style={{ fontSize: "0.72rem", color: "var(--text3)", marginTop: -4, marginBottom: 10, lineHeight: 1.5 }}>
                Планировку комнат внутри квартир расставит выбранная выше локальная модель.
                Каждый вариант проверяется по нормам КМК; если модель ошиблась или недоступна —
                автоматический откат на детерминированный генератор.
              </p>
            )}

            <textarea
              value={archReq}
              onChange={e => setArchReq(e.target.value)}
              disabled={archLoading}
              placeholder={"Примеры:\n• Жилой дом на семью из 4 человек, 2 этажа, 3 спальни, балконы\n• Офис на 20 сотрудников, представительский вход, переговорная\n• Торговый павильон 100м², одноэтажный, большие витрины"}
              style={{ width: "100%", height: 110, padding: 10, background: "var(--bg2)", border: "1px solid var(--border)", borderRadius: 8, color: "var(--text)", resize: "vertical", fontSize: "0.83rem", fontFamily: "inherit", lineHeight: 1.6 }}
            />

            {archError && (
              <div style={{ padding: "8px 12px", background: "rgba(255,69,58,0.1)", border: "1px solid rgba(255,69,58,0.25)", borderRadius: 8, fontSize: "0.78rem", color: "var(--danger)", margin: "10px 0" }}>
                ❌ {archError}
              </div>
            )}

            <button className="btn-gen" onClick={designWithAI} disabled={archLoading || !archReq.trim()} style={{ marginTop: 10 }}>
              {archLoading ? "⏳ Идёт проектирование..." : "🏛 Разработать проект"}
            </button>

            {/* Прогресс шагов */}
            {archLoading && (
              <div style={{ marginTop: 12 }}>
                {([
                  ["norms",      "📚 Изучение норм КМК/ШНК Узбекистана..."],
                  ["planning",   "📐 Составление плана здания по нормам..."],
                  ["generating", "🏗 Генерация IFC-модели..."],
                ] as [typeof archStep, string][]).map(([step, label]) => {
                  const steps = ["norms","planning","generating","done"];
                  const current = steps.indexOf(archStep);
                  const mine = steps.indexOf(step);
                  const done = current > mine || archStep === "done";
                  const active = archStep === step;
                  return (
                    <div key={step} style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 10px", marginBottom: 4, borderRadius: 7, background: active ? "rgba(10,132,255,0.12)" : "transparent" }}>
                      <div style={{ width: 18, height: 18, borderRadius: "50%", border: `2px solid ${done ? "var(--accent)" : active ? "var(--accent)" : "var(--border)"}`, background: done ? "var(--accent)" : "transparent", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
                        {done && <span style={{ color: "#fff", fontSize: "0.65rem" }}>✓</span>}
                      </div>
                      <span style={{ fontSize: "0.8rem", color: done || active ? "var(--text)" : "var(--text3)" }}>{label}</span>
                    </div>
                  );
                })}
              </div>
            )}

            {/* Результат: план с нормами */}
            {archStep === "done" && archReasoning && (<>
              <div style={{ marginTop: 14, padding: "12px 14px", background: "rgba(10,132,255,0.08)", border: "1px solid rgba(10,132,255,0.2)", borderRadius: 10 }}>
                <div style={{ fontWeight: 600, fontSize: "0.9rem", color: "var(--text)", marginBottom: 3 }}>{archName}</div>
                <div style={{ fontSize: "0.78rem", color: "var(--text2)", lineHeight: 1.6 }}>{archSummary}</div>
              </div>

              {/* Что изучил LLM */}
              {archNormStudy && (
                <div style={{ marginTop: 8, padding: "8px 12px", background: "rgba(48,209,88,0.08)", border: "1px solid rgba(48,209,88,0.2)", borderRadius: 8 }}>
                  <div style={{ fontSize: "0.68rem", fontWeight: 600, color: "#30d158", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 3 }}>📚 Применённые нормы</div>
                  <div style={{ fontSize: "0.77rem", color: "var(--text)", lineHeight: 1.6 }}>{archNormStudy}</div>
                </div>
              )}

              {/* Автоматические исправления — нормы, которые LLM не учла */}
              {archViolations.length > 0 && (
                <div style={{ marginTop: 8, padding: "8px 12px", background: "rgba(255,159,10,0.08)", border: "1px solid rgba(255,159,10,0.25)", borderRadius: 8 }}>
                  <div style={{ fontSize: "0.68rem", fontWeight: 600, color: "#ff9f0a", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 4 }}>
                    ⚠️ Автокоррекция по нормам ({archViolations.length})
                  </div>
                  <ul style={{ margin: 0, paddingLeft: 16, fontSize: "0.75rem", color: "var(--text)", lineHeight: 1.7 }}>
                    {archViolations.map((v, i) => <li key={i}>{v}</li>)}
                  </ul>
                </div>
              )}

              {/* Проверка целостности IFC */}
              {archIntegrity && (
                <div style={{
                  marginTop: 8, padding: "8px 12px", borderRadius: 8,
                  background: archIntegrity.ok ? "rgba(48,209,88,0.06)" : "rgba(255,69,58,0.07)",
                  border: `1px solid ${archIntegrity.ok ? "rgba(48,209,88,0.25)" : "rgba(255,69,58,0.3)"}`,
                }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: archIntegrity.issues.length ? 6 : 0 }}>
                    <span style={{ fontSize: "1rem" }}>{archIntegrity.ok ? "✅" : "❌"}</span>
                    <div style={{ fontSize: "0.68rem", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em", color: archIntegrity.ok ? "#30d158" : "var(--danger)" }}>
                      Целостность IFC — {archIntegrity.summary}
                    </div>
                    <div style={{ marginLeft: "auto", fontSize: "0.68rem", color: "var(--text3)" }}>
                      {archIntegrity.counts.total_elements} элементов
                    </div>
                  </div>
                  {archIntegrity.issues.length > 0 && (
                    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                      {archIntegrity.issues.map((issue, i) => (
                        <div key={i} style={{
                          padding: "5px 8px", borderRadius: 6, fontSize: "0.73rem",
                          background: issue.severity === "error" ? "rgba(255,69,58,0.1)" : "rgba(255,159,10,0.08)",
                          borderLeft: `3px solid ${issue.severity === "error" ? "var(--danger)" : "#ff9f0a"}`,
                        }}>
                          <span style={{ fontWeight: 600, color: issue.severity === "error" ? "var(--danger)" : "#ff9f0a", marginRight: 4 }}>
                            {issue.severity === "error" ? "ОШИБКА" : "ВНИМАНИЕ"}
                          </span>
                          <span style={{ color: "var(--text3)", marginRight: 4 }}>
                            {issue.element_type} / {issue.element_name}:
                          </span>
                          <span style={{ color: "var(--text)", lineHeight: 1.5 }}>{issue.message}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* Этапы строительства — снизу вверх */}
              {archStages.length > 0 && (
                <div style={{ marginTop: 8, padding: "8px 12px", background: "var(--bg2)", borderRadius: 8, border: "1px solid var(--border)" }}>
                  <div style={{ fontSize: "0.68rem", fontWeight: 600, color: "var(--text3)", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 6 }}>🏗 Этапы строительства</div>
                  {archStages.map((s, i) => (
                    <div key={i} style={{ display: "flex", gap: 10, padding: "6px 0", borderTop: i > 0 ? "1px solid var(--border)" : "none" }}>
                      <div style={{ width: 20, height: 20, borderRadius: "50%", background: "var(--accent)", color: "#fff", fontSize: "0.7rem", fontWeight: 700, display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>{i + 1}</div>
                      <div style={{ flex: 1 }}>
                        <div style={{ fontSize: "0.8rem", fontWeight: 600, color: "var(--text)" }}>{s.stage}</div>
                        <div style={{ fontSize: "0.75rem", color: "var(--text2)", lineHeight: 1.5, marginTop: 2 }}>{s.description}</div>
                        {s.norm_refs && s.norm_refs.length > 0 && (
                          <div style={{ fontSize: "0.68rem", color: "var(--text3)", marginTop: 2 }}>{s.norm_refs.join(", ")}</div>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {/* Лестнично-лифтовой узел и инженерные сети */}
              {archBuilding && (
                <div style={{ marginTop: 8, padding: "8px 12px", background: "var(--bg2)", borderRadius: 8, border: "1px solid var(--border)" }}>
                  <div style={{ fontSize: "0.68rem", fontWeight: 600, color: "var(--text3)", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 6 }}>🛗 Лестнично-лифтовой узел</div>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.75rem" }}>
                    <tbody>
                      {[
                        ["Подъездов", archBuilding.entrances],
                        ["Квартир на площадке", archBuilding.apartments_per_landing],
                        ["Лифт", archBuilding.has_elevator ? `да, ${archBuilding.elevators_per_entrance ?? 1} шт./подъезд` : "нет"],
                        archBuilding.has_elevator ? ["Грузоподъёмность лифта", archBuilding.elevator_capacity_kg ? `${archBuilding.elevator_capacity_kg} кг` : null] : null,
                        archBuilding.has_elevator ? ["Шахта лифта", archBuilding.elevator_shaft_m] : null,
                        ["Ширина марша", archBuilding.stair_width_m ? `${archBuilding.stair_width_m} м` : null],
                        ["Шахта водопровод/канализация", archBuilding.riser_shaft_m],
                        ["Ниша электрощита", archBuilding.electrical_niche_m],
                      ].filter((row): row is [string, any] => row !== null && row[1] != null && row[1] !== "")
                        .map(([label, val]) => (
                          <tr key={label} style={{ borderTop: "1px solid var(--border)" }}>
                            <td style={{ padding: "4px 0", color: "var(--text2)" }}>{label}</td>
                            <td style={{ padding: "4px 0", color: "var(--text)", textAlign: "right" }}>{String(val)}</td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
              )}

              {/* Таблица параметров плана */}
              {archPlan && (
                <div style={{ marginTop: 8, padding: "8px 12px", background: "var(--bg2)", borderRadius: 8, border: "1px solid var(--border)" }}>
                  <div style={{ fontSize: "0.68rem", fontWeight: 600, color: "var(--text3)", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 6 }}>📋 Расчётные параметры</div>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.75rem" }}>
                    <tbody>
                      {[
                        ["Общая площадь", archPlan.total_area_m2 ? `${archPlan.total_area_m2} м²` : null, archPlan.norm_ref_area],
                        ["На 1 чел.", archPlan.area_per_person ? `${archPlan.area_per_person} м²` : null, archPlan.norm_ref_area],
                        ["Высота этажа", archPlan.floor_height_m ? `${archPlan.floor_height_m} м` : null, archPlan.norm_ref_height],
                        ["Стены", archPlan.wall_material || (archPlan.wall_thickness_m ? `${(archPlan.wall_thickness_m * 1000).toFixed(0)} мм` : null), archPlan.norm_ref_wall],
                        ["Перекрытие", archPlan.slab_thickness_m ? `${(archPlan.slab_thickness_m * 1000).toFixed(0)} мм` : null, archPlan.norm_ref_slab],
                        ["Подоконник", archPlan.window_sill_m ? `${archPlan.window_sill_m} м` : null, archPlan.norm_ref_sill],
                        ["Перемычка", archPlan.lintel_height_m ? `${(archPlan.lintel_height_m * 1000).toFixed(0)} мм` : null, archPlan.norm_ref_lintel],
                        ["Фундамент", archPlan.foundation_depth_m ? `${archPlan.foundation_depth_m} м` : null, archPlan.norm_ref_foundation],
                      ].filter(([, v]) => v).map(([k, v, ref]) => (
                        <tr key={k as string} style={{ borderBottom: "1px solid var(--border)" }}>
                          <td style={{ padding: "4px 0", color: "var(--text2)", width: "38%" }}>{k}</td>
                          <td style={{ padding: "4px 4px", color: "var(--text)", fontWeight: 500 }}>{v}</td>
                          <td style={{ padding: "4px 0", color: "var(--accent)", fontSize: "0.68rem", textAlign: "right" }}>{ref}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {/* Архитектурные решения */}
              {([
                ["📐 Габариты", archReasoning.footprint],
                ["🏢 Этажность", archReasoning.floors],
                ["🪟 Фасад / окна", archReasoning.facade],
                ["🏗 Конструкция", archReasoning.structure],
                ["🗺 Планировка", archReasoning.layout],
              ] as [string, string][]).map(([label, text]) => text ? (
                <div key={label} style={{ marginTop: 6, padding: "8px 12px", background: "var(--bg2)", borderRadius: 8, border: "1px solid var(--border)" }}>
                  <div style={{ fontSize: "0.68rem", fontWeight: 600, color: "var(--text3)", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 3 }}>{label}</div>
                  <div style={{ fontSize: "0.78rem", color: "var(--text)", lineHeight: 1.6 }}>{text}</div>
                </div>
              ) : null)}

              <button className="btn-gen" onClick={() => setTab("view")} style={{ marginTop: 12, background: "var(--bg3)", color: "var(--text)" }}>
                Открыть 3D модель →
              </button>
            </>)}

            {archStep === "idle" && (
              <div style={{ marginTop: 12, padding: "10px 12px", background: "var(--bg2)", borderRadius: 8, fontSize: "0.73rem", color: "var(--text2)", lineHeight: 1.7 }}>
                <strong style={{ color: "var(--text3)" }}>Пайплайн:</strong><br />
                1. 📚 LLM изучает КМК/ШНК: высота этажа, толщина стен, размеры окон, фундамент<br />
                2. 📐 Составляет план с ссылками на нормы и расчётом площадей<br />
                3. 🏗 Генерирует IFC с правильными пропорциями
              </div>
            )}
          </>)}

          {/* ══ ПАРАМЕТРЫ ══ */}
          {tab === "create" && (<>
            {analysisNotes && (
              <div style={{ padding: "8px 12px", background: "rgba(10,132,255,0.1)", border: "1px solid rgba(10,132,255,0.25)", borderRadius: 8, fontSize: "0.78rem", color: "var(--accent)", marginBottom: 12 }}>
                🧠 {analysisNotes}
              </div>
            )}
            <h3 style={{ marginBottom: 12, color: "var(--accent)" }}>Параметры здания</h3>

            <Label>Название<input value={params.name} onChange={e => up("name", e.target.value)} /></Label>

            <Divider label="Размеры" />
            <Label>Длина (м)<input type="number" value={params.length} onChange={e => up("length", +e.target.value)} min={4} max={200} step={0.5} /></Label>
            <Label>Ширина (м)<input type="number" value={params.width} onChange={e => up("width", +e.target.value)} min={4} max={100} step={0.5} /></Label>
            <Label>Этажей<input type="number" value={params.num_floors} onChange={e => up("num_floors", Math.max(1, +e.target.value))} min={1} max={20} /></Label>
            <Label>Высота этажа (м)<input type="number" value={+(params.height / params.num_floors).toFixed(1)} onChange={e => up("height", +e.target.value * params.num_floors)} min={2.5} max={6} step={0.1} /></Label>

            <Divider label="Крыша" />
            <Label>Тип крыши
              <select value={params.roof_type} onChange={e => up("roof_type", e.target.value)}>
                <option value="gable">Двускатная</option>
                <option value="flat">Плоская с парапетом</option>
              </select>
            </Label>

            <Divider label="Окна" />
            <Label>Окон по длинной стене<input type="number" value={params.windows_per_wall_long} onChange={e => up("windows_per_wall_long", Math.max(0, +e.target.value))} min={0} max={10} /></Label>
            <Label>Окон по короткой стене<input type="number" value={params.windows_per_wall_short} onChange={e => up("windows_per_wall_short", Math.max(0, +e.target.value))} min={0} max={6} /></Label>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
              <Label>Ширина (м)<input type="number" value={params.window_width} onChange={e => up("window_width", +e.target.value)} min={0.4} max={3} step={0.05} /></Label>
              <Label>Высота (м)<input type="number" value={params.window_height} onChange={e => up("window_height", +e.target.value)} min={0.5} max={3} step={0.05} /></Label>
            </div>
            <Label>Подоконник (м)<input type="number" value={params.window_sill} onChange={e => up("window_sill", +e.target.value)} min={0.3} max={1.5} step={0.05} /></Label>

            <Divider label="Двери" />
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
              <Label>Ширина (м)<input type="number" value={params.door_width} onChange={e => up("door_width", +e.target.value)} min={0.6} max={2} step={0.05} /></Label>
              <Label>Высота (м)<input type="number" value={params.door_height} onChange={e => up("door_height", +e.target.value)} min={1.8} max={3} step={0.05} /></Label>
            </div>

            <Divider label="Элементы" />
            <CheckRow label="Окна" checked={params.add_windows} onChange={v => up("add_windows", v)} />
            <CheckRow label="Двери" checked={params.add_doors} onChange={v => up("add_doors", v)} />
            <CheckRow label="Колонны" checked={params.add_columns} onChange={v => up("add_columns", v)} />
            <CheckRow label="Перегородки" checked={params.add_internal_walls} onChange={v => up("add_internal_walls", v)} />
            <CheckRow label="Балконы" checked={params.add_balconies} onChange={v => up("add_balconies", v)} />
            <CheckRow label="Фундамент" checked={params.add_foundation} onChange={v => up("add_foundation", v)} />

            <button className="btn-gen" onClick={generate} disabled={loading} style={{ marginTop: 14 }}>
              {loading ? "⏳ Генерация..." : "Сгенерировать IFC"}
            </button>
          </>)}

          {/* ══ ПО ПЛАНУ ══ */}
          {tab === "plan" && (<>
            <h3 style={{ marginBottom: 8, color: "var(--accent)" }}>📐 Анализ плана / фасада</h3>
            <p style={{ fontSize: "0.78rem", color: "var(--text2)", marginBottom: 14, lineHeight: 1.6 }}>
              Загрузи фото плана, фасада или скетча. LLM извлечёт параметры здания — потом можно скорректировать и сгенерировать IFC.
            </p>

            {/* Drag zone */}
            <div
              onClick={() => planInputRef.current?.click()}
              onDrop={e => { e.preventDefault(); const f = e.dataTransfer.files[0]; if (f) setPlanFile(f); }}
              onDragOver={e => e.preventDefault()}
              style={{
                border: `2px dashed ${planFile ? "var(--accent)" : "var(--border)"}`,
                borderRadius: 10, padding: "20px 16px", textAlign: "center",
                cursor: "pointer", marginBottom: 12, transition: "border-color 0.2s",
                background: planFile ? "rgba(10,132,255,0.05)" : "transparent",
              }}>
              <div style={{ fontSize: "1.8rem", marginBottom: 6 }}>🗂️</div>
              {planFile
                ? <><strong style={{ color: "var(--text)", fontSize: "0.85rem" }}>{planFile.name}</strong><br />
                    <span style={{ fontSize: "0.72rem", color: "var(--text2)" }}>{(planFile.size/1024).toFixed(0)} KB</span></>
                : <span style={{ fontSize: "0.82rem", color: "var(--text2)" }}>
                    Перетащи или нажми<br />
                    <span style={{ fontSize: "0.72rem" }}>JPG · PNG · PDF · скетч</span>
                  </span>
              }
              <input ref={planInputRef} type="file" accept="image/*,.pdf" style={{ display: "none" }}
                onChange={e => { const f = e.target.files?.[0]; if (f) setPlanFile(f); }} />
            </div>

            <Label>Дополнительное описание (необязательно)
              <textarea
                value={planDesc}
                onChange={e => setPlanDesc(e.target.value)}
                placeholder="Напр: 3-этажный жилой дом, фасадные окна 1.5×1.8м, балконы на 2 и 3 этаже"
                style={{ width: "100%", height: 80, padding: 9, background: "var(--bg2)", border: "1px solid var(--border)", borderRadius: 8, color: "var(--text)", resize: "vertical", fontSize: "0.82rem", fontFamily: "inherit" }}
              />
            </Label>

            {analysisError && (
              <div style={{ padding: "8px 12px", background: "rgba(255,69,58,0.1)", border: "1px solid rgba(255,69,58,0.25)", borderRadius: 8, fontSize: "0.78rem", color: "var(--danger)", marginBottom: 10 }}>
                ❌ {analysisError}
              </div>
            )}

            <button className="btn-gen" onClick={analyzeImage}
              disabled={imgLoading || (!planFile && !planDesc.trim())}>
              {imgLoading ? "⏳ Анализирую..." : "🔍 Извлечь параметры"}
            </button>

            <div style={{ marginTop: 12, padding: "10px 12px", background: "var(--bg2)", borderRadius: 8, fontSize: "0.75rem", color: "var(--text2)", lineHeight: 1.7 }}>
              <strong style={{ color: "var(--text3)" }}>Как работает:</strong><br />
              1. Загрузи план этажа или фасад<br />
              2. LLM (LM Studio) распознаёт размеры, окна, этажность<br />
              3. Параметры автозаполняются во вкладке «Параметры»<br />
              4. Проверь и нажми «Сгенерировать IFC»<br />
              <span style={{ color: "var(--accent)", fontSize: "0.72rem" }}>* Нужна vision-модель (Qwen2-VL, LLaVA) в LM Studio</span>
            </div>
          </>)}

          {/* ══ БЫСТРЫЙ ЭСКИЗ ══ */}
          {tab === "nl" && (<>
            <h3 style={{ marginBottom: 8, color: "var(--accent)" }}>🤖 Быстрый эскиз по описанию</h3>
            <p style={{ fontSize: "0.78rem", color: "var(--text2)", marginBottom: 8, lineHeight: 1.6 }}>
              Опиши здание текстом — свободная планировка комнат за секунды, без обращения к базе норм.
              Подходит для черновика/визуализации идеи.
            </p>
            <div style={{ padding: "8px 12px", marginBottom: 12, background: "rgba(255,159,10,0.08)", border: "1px solid rgba(255,159,10,0.25)", borderRadius: 8, fontSize: "0.75rem", color: "var(--text)", lineHeight: 1.6 }}>
              ⚠️ Этот режим <b>не проверяет</b> здание по КМК/ШНК и не считает целостность модели.
              Для реального проекта (толщина стен, лифты по нормам, планировка квартир) используйте
              вкладку «🏛 Архитектор».
            </div>
            <textarea
              value={description}
              onChange={e => setDescription(e.target.value)}
              placeholder={"Пример:\nДвухэтажный жилой дом 12×10м, двускатная крыша.\n3 спальни, кухня, гостиная. Балкон на втором этаже."}
              style={{ width: "100%", height: 130, padding: 10, background: "var(--bg2)", border: "1px solid var(--border)", borderRadius: 8, color: "var(--text)", resize: "vertical", fontSize: "0.85rem", fontFamily: "inherit" }}
            />
            <button className="btn-gen" onClick={generateFromDesc} disabled={nlLoading || !description.trim()} style={{ marginTop: 10 }}>
              {nlLoading ? "⏳ Парсинг..." : "🤖 Сгенерировать эскиз"}
            </button>
          </>)}

          {/* ══ ПРОСМОТР ══ */}
          {tab === "view" && (<>
            <h3 style={{ marginBottom: 12, color: "var(--accent)" }}>Готовые модели</h3>
            {files.length === 0 && <p style={{ color: "var(--text2)", fontSize: "0.85rem" }}>Нет моделей</p>}
            <div className="model-files">
              {files.map(f => (
                <div key={f.name} className="model-file"
                  style={{ cursor: "pointer", borderColor: selectedFile === f.name ? "var(--accent)" : undefined }}
                  onClick={() => setSelectedFile(f.name)}>
                  <span style={{ fontSize: "0.8rem", flex: 1, wordBreak: "break-all" }}>{f.name}</span>
                  <div style={{ display: "flex", gap: 6, alignItems: "center", flexShrink: 0 }}>
                    <span style={{ color: "var(--text2)", fontSize: "0.72rem" }}>{f.size_kb}KB</span>
                    <a href={f.url} download onClick={e => e.stopPropagation()} style={{ color: "var(--accent)", fontSize: "0.85rem" }}>⬇</a>
                    <span onClick={async e => {
                      e.stopPropagation();
                      if (confirm(`Удалить ${f.name}?`)) {
                        await fetch(`${API}/api/model/delete/${f.name}`, { method: "DELETE" });
                        await refreshFiles();
                        if (selectedFile === f.name) setSelectedFile(null);
                      }
                    }} style={{ color: "var(--danger)", cursor: "pointer", fontSize: "0.85rem" }}>✕</span>
                  </div>
                </div>
              ))}
            </div>
            {stats && (<>
              <h3 style={{ margin: "14px 0 8px" }}>Статистика</h3>
              <div className="model-stats">
                {Object.entries(stats).map(([k, v]) => (
                  <div key={k} className="model-stat">
                    <div className="stat-value">{v}</div>
                    <div className="stat-label">{k}</div>
                  </div>
                ))}
              </div>
            </>)}
            <p style={{ fontSize: "0.75rem", color: "var(--text2)", marginTop: 12 }}>
              🖱 ЛКМ вращение · ПКМ панорама · Колёсико зум
            </p>
          </>)}
        </div>

        {/* ─── Right: 3D viewer ─── */}
        <div className="modeling-viewer">
          {selectedFile && (tab === "view" || tab === "create" || tab === "nl" || tab === "arch") ? (
            <ThreeViewer filename={selectedFile} />
          ) : (
            <div className="viewer-placeholder">
              <div style={{ fontSize: "3rem", opacity: 0.3 }}>🏗️</div>
              <div style={{ textAlign: "center", maxWidth: 300, lineHeight: 1.7 }}>
                {tab === "plan"
                  ? "Загрузи план — LLM извлечёт параметры, затем сгенерируй IFC"
                  : tab === "arch"
                  ? "Опиши требования — AI спроектирует здание и покажет его здесь"
                  : "Задай параметры и нажми «Сгенерировать»"
                }
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
