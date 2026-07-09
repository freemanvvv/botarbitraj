import { useState, useEffect, useRef } from "react";
import ThreeViewer from "../components/ThreeViewer";
import PlanEditor from "../components/PlanEditor";

// В проде фронт отдаётся тем же бэкендом → относительный base (origin
// страницы), иначе localhost↔127.0.0.1 = разные origin и CORS роняет
// запросы («Failed to fetch»). В dev (vite :5173) — абсолютный адрес.
const API = import.meta.env.DEV ? "http://localhost:8765" : "";

type BuildingKind = "house" | "apartment";

interface ChatMessage {
  role: "user" | "bot";
  content: string;
}

interface Floor {
  level: number;
  label: string;
  svg: string;
  area_m2: number;
}

interface NormsIssue {
  severity: "error" | "warning" | "info";
  element_type: string;
  element_name: string;
  message: string;
}

interface PlanResponse {
  building_kind: BuildingKind;
  summary: string;
  floors: Floor[];
  norms_issues: NormsIssue[];
  norms_citations: string;
  raw_program: any;
  raw_floorplan: any;
  variant?: number;
  variant_count?: number;
}

interface BuildStats {
  [key: string]: any;
}

interface SavedPlan {
  id: number;
  building_kind: BuildingKind;
  name: string;
  description: string;
  created_at: string;
}

export default function Modeling() {
  const [buildingKind, setBuildingKind] = useState<BuildingKind | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [checkNorms, setCheckNorms] = useState(true);
  const [models, setModels] = useState<string[]>(["local-model"]);
  const [model, setModel] = useState("local-model");

  const [planLoading, setPlanLoading] = useState(false);
  const [lastDescription, setLastDescription] = useState("");
  const [plan, setPlan] = useState<PlanResponse | null>(null);
  const [page, setPage] = useState(0);

  const [savedPlanId, setSavedPlanId] = useState<number | null>(null);
  const [saveLoading, setSaveLoading] = useState(false);
  const [editing, setEditing] = useState(false);
  const [dxfLoading, setDxfLoading] = useState(false);

  const [build3dLoading, setBuild3dLoading] = useState(false);
  const [build3dError, setBuild3dError] = useState("");
  const [stats, setStats] = useState<BuildStats | null>(null);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [rightView, setRightView] = useState<"album" | "3d">("album");

  const [savedPlans, setSavedPlans] = useState<SavedPlan[]>([]);
  const [openLoading, setOpenLoading] = useState<number | null>(null);

  const messagesEndRef = useRef<HTMLDivElement>(null);

  const refreshSavedPlans = () => {
    fetch(`${API}/api/house/plans`)
      .then(r => r.json())
      .then(d => setSavedPlans(d.plans || []))
      .catch(() => {});
  };

  useEffect(() => {
    fetch(`${API}/api/chat/models`)
      .then(r => r.json())
      .then(d => { if (d.models?.length) { setModels(d.models); setModel(d.models[0]); } })
      .catch(() => {});
    refreshSavedPlans();
  }, []);

  const openSavedPlan = async (id: number) => {
    setOpenLoading(id);
    try {
      const res = await fetch(`${API}/api/house/${id}`);
      const d = await res.json();
      if (!res.ok) throw new Error(d.detail || "Не удалось открыть проект");
      setBuildingKind(d.building_kind);
      setPlan(d);
      setPage(0);
      setLastDescription(d.description || "");
      setSavedPlanId(d.id);
      setStats(null); setSelectedFile(null); setRightView("album"); setEditing(false);
      setMessages([{ role: "bot", content: `📂 Открыт сохранённый проект «${d.name}».` }]);
    } catch (e: any) {
      alert(e.message);
    } finally {
      setOpenLoading(null);
    }
  };

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const resetForNewObject = () => {
    setMessages([]); setInput(""); setPlan(null); setPage(0);
    setSavedPlanId(null); setStats(null); setSelectedFile(null);
    setRightView("album"); setBuild3dError(""); setEditing(false);
  };

  const generatePlan = async (description: string, opts: { variant?: number; program?: any } = {}) => {
    if (!description.trim() || !buildingKind) return;
    setPlanLoading(true);
    try {
      const res = await fetch(`${API}/api/house/plan`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          building_kind: buildingKind, description, model, check_norms: checkNorms,
          variant: opts.variant ?? 0, program: opts.program ?? null,
        }),
      });
      const d = await res.json();
      if (!res.ok) throw new Error(d.detail || "Ошибка генерации плана");
      setPlan(d);
      setPage(0);
      setSavedPlanId(null);
      setStats(null); setSelectedFile(null); setRightView("album"); setEditing(false);
      setLastDescription(description);
      const errCount = d.norms_issues.filter((i: NormsIssue) => i.severity === "error").length;
      const vc = d.variant_count ?? 1;
      const variantNote = vc > 1 ? ` Форм из датасета под этот состав: ${vc} (вариант ${(d.variant ?? 0) + 1}/${vc}).` : "";
      const summary = errCount > 0
        ? `Готово: «${d.summary}», ${d.floors.length} этаж(а/ей). Найдено нарушений норм: ${errCount} — можно перегенерировать или сохранить как есть.${variantNote}`
        : `Готово: «${d.summary}», ${d.floors.length} этаж(а/ей). Нарушений норм не найдено.${variantNote}`;
      setMessages(prev => [...prev, { role: "bot", content: summary }]);
    } catch (e: any) {
      setMessages(prev => [...prev, { role: "bot", content: `❌ ${e.message}` }]);
    } finally {
      setPlanLoading(false);
    }
  };

  const sendMessage = () => {
    if (!input.trim() || planLoading) return;
    const text = input.trim();
    setInput("");
    setMessages(prev => [...prev, { role: "user", content: text }]);
    generatePlan(text);
  };

  // Перегенерировать — новый состав от LLM (другая температура) с нуля.
  const regenerate = () => {
    if (!lastDescription || planLoading) return;
    setMessages(prev => [...prev, { role: "user", content: "🔄 Перегенерировать" }]);
    generatePlan(lastDescription);
  };

  // Другой вариант — та же комплектация, следующая РЕАЛЬНАЯ форма из датасета
  // (без обращения к LLM: переиспользуем уже полученный BuildingProgram).
  const showVariant = () => {
    if (!plan || planLoading) return;
    const vc = plan.variant_count ?? 1;
    if (vc <= 1) return;
    const next = ((plan.variant ?? 0) + 1) % vc;
    setMessages(prev => [...prev, { role: "user", content: `🔀 Другой вариант (${next + 1}/${vc})` }]);
    generatePlan(lastDescription, { variant: next, program: plan.raw_program });
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  const savePlan = async () => {
    if (!plan) return;
    setSaveLoading(true);
    try {
      const res = await fetch(`${API}/api/house/save`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          building_kind: plan.building_kind,
          name: plan.summary || "Проект",
          description: lastDescription,
          program: plan.raw_program,
          floorplan: plan.raw_floorplan,
          norms_issues: plan.norms_issues,
          norms_citations: plan.norms_citations,
        }),
      });
      const d = await res.json();
      if (!res.ok) throw new Error(d.detail || "Ошибка сохранения");
      setSavedPlanId(d.plan_id);
      setMessages(prev => [...prev, { role: "bot", content: "💾 План сохранён. Можно построить 3D-модель." }]);
      refreshSavedPlans();
    } catch (e: any) {
      setMessages(prev => [...prev, { role: "bot", content: `❌ ${e.message}` }]);
    } finally {
      setSaveLoading(false);
    }
  };

  // Правки редактора применены: обновляем этажи/нормы/геометрию. Геометрия
  // изменилась → черновик надо сохранить заново (сбрасываем savedPlanId).
  const applyEdits = (res: { floors: Floor[]; norms_issues: NormsIssue[]; raw_program: any; raw_floorplan: any }) => {
    setPlan(prev => prev ? {
      ...prev,
      floors: res.floors,
      norms_issues: res.norms_issues,
      raw_program: res.raw_program,
      raw_floorplan: res.raw_floorplan,
    } : prev);
    setSavedPlanId(null);
    setStats(null); setSelectedFile(null); setRightView("album");
    setEditing(false);
    const errCount = res.norms_issues.filter(i => i.severity === "error").length;
    setMessages(prev => [...prev, { role: "bot", content: `✏️ Правки применены. Нарушений норм: ${errCount}. Сохраните проект, чтобы построить 3D.` }]);
  };

  // Экспорт плана в DXF (САПР): контуры, стены, проёмы, размерные линии,
  // экспликация. Работает с текущей геометрией альбома (в т.ч. до сохранения).
  const downloadDxf = async () => {
    if (!plan) return;
    setDxfLoading(true);
    try {
      const res = await fetch(`${API}/api/house/dxf`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ building_kind: "house", program: plan.raw_program, floorplan: plan.raw_floorplan }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.detail || "Ошибка экспорта DXF");
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${(plan.summary || "plan").replace(/[^\w\-.а-яА-Я ]+/g, "_")}.dxf`;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
    } catch (e: any) {
      setMessages(prev => [...prev, { role: "bot", content: `❌ ${e.message}` }]);
    } finally {
      setDxfLoading(false);
    }
  };

  const build3d = async () => {
    if (!savedPlanId) return;
    setBuild3dLoading(true);
    setBuild3dError("");
    try {
      const res = await fetch(`${API}/api/house/${savedPlanId}/build-3d`, { method: "POST" });
      const d = await res.json();
      if (!res.ok) throw new Error(d.detail || "Ошибка генерации 3D-модели");
      setStats(d.stats);
      setSelectedFile(d.filename);
      setRightView("3d");
    } catch (e: any) {
      setBuild3dError(e.message);
    } finally {
      setBuild3dLoading(false);
    }
  };

  // ══ Шаг 0: выбор типа объекта ══
  if (!buildingKind) {
    return (
      <div style={{ height: "calc(70vh + 40px)", display: "flex", flexDirection: "column" }}>
        <h2 style={{ marginBottom: 18 }}>Моделирование</h2>
        <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", gap: 24 }}>
          {([
            ["house", "🏡", "Частный сектор", "Отдельный дом, этажи с произвольным составом помещений"],
            ["apartment", "🏢", "Многоквартирный дом", "Подъезды, квартиры на лестничной площадке"],
          ] as [BuildingKind, string, string, string][]).map(([kind, icon, title, desc]) => (
            <button
              key={kind}
              onClick={() => setBuildingKind(kind)}
              style={{
                width: 280, padding: "36px 24px", background: "var(--bg1)", border: "1px solid var(--border2)",
                borderRadius: "var(--radius)", cursor: "pointer", textAlign: "center", color: "var(--text)",
                fontFamily: "inherit", transition: "border-color 0.15s, transform 0.15s",
              }}
              onMouseEnter={e => (e.currentTarget.style.borderColor = "var(--accent)")}
              onMouseLeave={e => (e.currentTarget.style.borderColor = "var(--border2)")}
            >
              <div style={{ fontSize: "2.6rem", marginBottom: 12 }}>{icon}</div>
              <div style={{ fontSize: "1.05rem", fontWeight: 600, marginBottom: 6 }}>{title}</div>
              <div style={{ fontSize: "0.8rem", color: "var(--text2)", lineHeight: 1.5 }}>{desc}</div>
            </button>
          ))}
        </div>

        {savedPlans.length > 0 && (
          <div style={{ marginTop: 8, maxWidth: 640, marginLeft: "auto", marginRight: "auto", width: "100%" }}>
            <div style={{ fontSize: "0.75rem", color: "var(--text3)", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 8 }}>
              📂 Мои проекты
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6, maxHeight: 220, overflowY: "auto" }}>
              {savedPlans.map(p => (
                <button
                  key={p.id}
                  onClick={() => openSavedPlan(p.id)}
                  disabled={openLoading === p.id}
                  style={{
                    display: "flex", alignItems: "center", gap: 10, padding: "9px 14px",
                    background: "var(--bg1)", border: "1px solid var(--border2)", borderRadius: 8,
                    cursor: openLoading === p.id ? "default" : "pointer", color: "var(--text)",
                    fontFamily: "inherit", textAlign: "left", fontSize: "0.83rem",
                  }}
                >
                  <span>{p.building_kind === "house" ? "🏡" : "🏢"}</span>
                  <span style={{ flex: 1 }}>{p.name || "Без названия"}</span>
                  <span style={{ fontSize: "0.72rem", color: "var(--text3)" }}>
                    {openLoading === p.id ? "⏳" : new Date(p.created_at).toLocaleDateString()}
                  </span>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    );
  }

  const floor = plan?.floors[page];
  const errCount = plan?.norms_issues.filter(i => i.severity === "error").length ?? 0;
  const warnCount = plan?.norms_issues.filter(i => i.severity === "warning").length ?? 0;

  return (
    <div style={{ height: "calc(70vh + 40px)", display: "flex", flexDirection: "column" }}>
      <div style={{ marginBottom: 14, display: "flex", alignItems: "center", gap: 10 }}>
        <h2>Моделирование</h2>
        <span style={{ fontSize: "0.82rem", color: "var(--text2)" }}>
          {buildingKind === "house" ? "🏡 Частный сектор" : "🏢 Многоквартирный дом"}
        </span>
        <button
          onClick={() => { setBuildingKind(null); resetForNewObject(); }}
          style={{ marginLeft: "auto", padding: "5px 12px", background: "var(--bg2)", border: "1px solid var(--border)", borderRadius: 8, color: "var(--text2)", cursor: "pointer", fontSize: "0.78rem" }}
        >
          ← Другой объект
        </button>
      </div>

      <div className="modeling-grid" style={{ flex: 1 }}>
        {/* ─── Left: chat ─── */}
        <div className="modeling-params" style={{ display: "flex", flexDirection: "column", padding: 12 }}>
          <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 10, flexWrap: "wrap" }}>
            <select value={model} onChange={e => setModel(e.target.value)} style={{ flex: 1, minWidth: 120 }}>
              {models.map(m => <option key={m} value={m}>{m}</option>)}
            </select>
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: "0.78rem", color: "var(--text2)", cursor: "pointer", whiteSpace: "nowrap" }}>
              <input type="checkbox" checked={checkNorms} onChange={e => setCheckNorms(e.target.checked)} style={{ accentColor: "var(--accent)" }} />
              Нормы КМК/ШНК (RAG)
            </label>
          </div>

          <div style={{ flex: 1, overflowY: "auto", background: "var(--bg2)", border: "1px solid var(--border)", borderRadius: 8, padding: 10, marginBottom: 8, display: "flex", flexDirection: "column", gap: 8, minHeight: 120 }}>
            {messages.length === 0 && (
              <div style={{ fontSize: "0.78rem", color: "var(--text3)", padding: 8, lineHeight: 1.6 }}>
                {buildingKind === "house"
                  ? "Опиши объект, например: «двухэтажный дом с 3 спальнями, 2 санузлами, детской, кухней, площадь участка 70 м²»."
                  : "Опиши объект, например: «жилой дом, 1 подъезд, 2 квартиры на площадке, 2-комнатные, 3 этажа, без лифта»."}
              </div>
            )}
            {messages.map((m, i) => (
              <div key={i} className={`msg ${m.role === "user" ? "user" : "bot"}`}>{m.content}</div>
            ))}
            {planLoading && <div className="msg bot" style={{ fontStyle: "italic" }}>⏳ Строю план...</div>}
            <div ref={messagesEndRef} />
          </div>

          <div className="chat-input">
            <textarea
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={planLoading}
              placeholder="Опиши объект..."
              rows={2}
            />
            <button onClick={sendMessage} disabled={planLoading || !input.trim()}>
              {planLoading ? "..." : "→"}
            </button>
          </div>
        </div>

        {/* ─── Right: album / 3D ─── */}
        <div className="modeling-viewer">
          {rightView === "3d" && selectedFile ? (
            <>
              <ThreeViewer filename={selectedFile} />
              <div style={{ position: "absolute", top: 10, left: 10, zIndex: 5, display: "flex", gap: 6 }}>
                {/* rightView is narrowed to "3d" in this branch — "План" is never active here */}
                <button className="toggle-btn" onClick={() => setRightView("album")}>📐 План</button>
                <button className="toggle-btn active" onClick={() => setRightView("3d")}>🧱 3D</button>
              </div>
            </>
          ) : plan && floor ? (
            <div style={{ height: "100%", display: "flex", flexDirection: "column", padding: 14, overflowY: "auto" }}>
              {selectedFile && (
                <div style={{ display: "flex", gap: 6, marginBottom: 10 }}>
                  <button className={`toggle-btn ${rightView === "album" ? "active" : ""}`} onClick={() => setRightView("album")}>📐 План</button>
                  <button className={`toggle-btn ${rightView === "3d" ? "active" : ""}`} onClick={() => setRightView("3d")}>🧱 3D</button>
                </div>
              )}

              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
                <div style={{ fontWeight: 600, fontSize: "0.92rem" }}>{floor.label}</div>
                <div style={{ fontSize: "0.78rem", color: "var(--text2)" }}>{floor.area_m2} м²</div>
                {(plan.variant_count ?? 1) > 1 && (
                  <span style={{ fontSize: "0.75rem", color: "var(--accent)", padding: "2px 8px", background: "rgba(10,132,255,0.12)", borderRadius: 6 }}>
                    🔀 форма {(plan.variant ?? 0) + 1} из {plan.variant_count}
                  </span>
                )}
                {savedPlanId && <span style={{ marginLeft: "auto", fontSize: "0.75rem", color: "#30d158" }}>✅ Сохранено (#{savedPlanId})</span>}
              </div>

              {editing && plan.building_kind === "house" ? (
                <PlanEditor
                  program={plan.raw_program}
                  floorplan={plan.raw_floorplan}
                  page={page}
                  onCancel={() => setEditing(false)}
                  onApplied={applyEdits}
                />
              ) : (
                <div style={{ background: "#fff", borderRadius: 8, padding: 10, flexShrink: 0 }}
                     dangerouslySetInnerHTML={{ __html: floor.svg }} />
              )}

              {!editing && plan.floors.length > 1 && (
                <div className="pagination">
                  {plan.floors.map((f, i) => (
                    <button key={f.level} className={i === page ? "active" : ""} onClick={() => setPage(i)}>
                      {f.label.replace("Этаж ", "").replace("Квартира — вход с ", "")}
                    </button>
                  ))}
                </div>
              )}

              {plan.norms_issues.length > 0 && (
                <div style={{ marginTop: 12, padding: "8px 12px", borderRadius: 8, background: errCount ? "rgba(255,69,58,0.07)" : "rgba(255,159,10,0.08)", border: `1px solid ${errCount ? "rgba(255,69,58,0.3)" : "rgba(255,159,10,0.25)"}` }}>
                  <div style={{ fontSize: "0.68rem", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 6, color: errCount ? "var(--danger)" : "#ff9f0a" }}>
                    ⚠️ Нормы: {errCount} ошибок, {warnCount} предупреждений
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", gap: 4, maxHeight: 160, overflowY: "auto" }}>
                    {plan.norms_issues.map((issue, i) => (
                      <div key={i} style={{
                        padding: "5px 8px", borderRadius: 6, fontSize: "0.73rem",
                        background: issue.severity === "error" ? "rgba(255,69,58,0.1)" : "rgba(255,159,10,0.08)",
                        borderLeft: `3px solid ${issue.severity === "error" ? "var(--danger)" : "#ff9f0a"}`,
                      }}>
                        <span style={{ color: "var(--text3)", marginRight: 4 }}>{issue.element_name}:</span>
                        <span style={{ color: "var(--text)" }}>{issue.message}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {plan.norms_citations && (
                <details style={{ marginTop: 8, padding: "8px 12px", background: "rgba(48,209,88,0.08)", border: "1px solid rgba(48,209,88,0.2)", borderRadius: 8 }}>
                  <summary style={{ cursor: "pointer", userSelect: "none", fontSize: "0.68rem", fontWeight: 600, color: "#30d158", textTransform: "uppercase", letterSpacing: "0.06em" }}>
                    📚 Применённые нормы (КМК/ШНК, RAG)
                  </summary>
                  <pre style={{ marginTop: 8, marginBottom: 0, whiteSpace: "pre-wrap", fontFamily: "inherit", fontSize: "0.75rem", color: "var(--text)", lineHeight: 1.6, maxHeight: 220, overflowY: "auto" }}>
                    {plan.norms_citations}
                  </pre>
                </details>
              )}

              {!editing && (
              <div style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap" }}>
                {(plan.variant_count ?? 1) > 1 && (
                  <button className="btn-gen" onClick={showVariant} disabled={planLoading} style={{ background: "var(--bg3)", color: "var(--text)" }}>
                    🔀 Другой вариант формы
                  </button>
                )}
                {plan.building_kind === "house" && (
                  <button className="btn-gen" onClick={() => setEditing(true)} disabled={planLoading} style={{ background: "var(--bg3)", color: "var(--text)" }}>
                    ✏️ Редактировать
                  </button>
                )}
                {plan.building_kind === "house" && (
                  <button className="btn-gen" onClick={downloadDxf} disabled={planLoading || dxfLoading} style={{ background: "var(--bg3)", color: "var(--text)" }}>
                    {dxfLoading ? "⏳..." : "📐 Скачать DXF"}
                  </button>
                )}
                <button className="btn-gen" onClick={regenerate} disabled={planLoading} style={{ background: "var(--bg3)", color: "var(--text)" }}>
                  🔄 Перегенерировать
                </button>
                <button className="btn-gen" onClick={savePlan} disabled={saveLoading || !!savedPlanId}>
                  {saveLoading ? "⏳..." : savedPlanId ? "✅ Сохранено" : "💾 Сохранить"}
                </button>
              </div>
              )}

              {!editing && savedPlanId && (
                <button className="btn-gen" onClick={build3d} disabled={build3dLoading} style={{ marginTop: 8 }}>
                  {build3dLoading ? "⏳ Строю 3D-модель..." : "🧱 Сгенерировать 3D модель"}
                </button>
              )}
              {build3dError && (
                <div style={{ marginTop: 8, padding: "8px 12px", background: "rgba(255,69,58,0.1)", border: "1px solid rgba(255,69,58,0.25)", borderRadius: 8, fontSize: "0.78rem", color: "var(--danger)" }}>
                  ❌ {build3dError}
                </div>
              )}
              {stats && (
                <div className="model-stats">
                  {Object.entries(stats).filter(([, v]) => typeof v === "number").map(([k, v]) => (
                    <div key={k} className="model-stat">
                      <div className="stat-value">{v as number}</div>
                      <div className="stat-label">{k}</div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <div className="viewer-placeholder">
              <div style={{ fontSize: "3rem", opacity: 0.3 }}>🏗️</div>
              <div style={{ textAlign: "center", maxWidth: 300, lineHeight: 1.7 }}>
                Опиши объект в чате слева — здесь появится план с размерами
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
