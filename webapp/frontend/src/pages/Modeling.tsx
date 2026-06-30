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

type Tab = "create" | "plan" | "nl" | "view";

export default function Modeling() {
  const [params, setParams] = useState({ ...DEFAULT_PARAMS });
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(false);
  const [nlLoading, setNlLoading] = useState(false);
  const [imgLoading, setImgLoading] = useState(false);
  const [files, setFiles] = useState<IFCFile[]>([]);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("create");
  const [description, setDescription] = useState("");
  const [planFile, setPlanFile] = useState<File | null>(null);
  const [planDesc, setPlanDesc] = useState("");
  const [analysisNotes, setAnalysisNotes] = useState("");
  const [analysisError, setAnalysisError] = useState("");
  const planInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    fetch(`${API}/api/model/existing`)
      .then(r => r.json())
      .then(d => { setFiles(d.files); if (d.files.length) setSelectedFile(d.files[0].name); })
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

  const fmt = (ts: number) => new Date(ts * 1000).toLocaleString().slice(0, -3);

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
            ["create", "Параметры"],
            ["plan",   "📐 По плану"],
            ["nl",     "🤖 По описанию"],
            ["view",   "Просмотр"],
          ] as [Tab, string][]).map(([k, l]) => (
            <button key={k} className={`toggle-btn ${tab === k ? "active" : ""}`} onClick={() => setTab(k)}>{l}</button>
          ))}
        </div>
      </div>

      <div className="modeling-grid" style={{ flex: 1 }}>
        {/* ─── Left panel ─── */}
        <div className="modeling-params">

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

          {/* ══ ПО ОПИСАНИЮ ══ */}
          {tab === "nl" && (<>
            <h3 style={{ marginBottom: 8, color: "var(--accent)" }}>🤖 Модель по описанию</h3>
            <p style={{ fontSize: "0.78rem", color: "var(--text2)", marginBottom: 12, lineHeight: 1.6 }}>
              Опиши здание текстом — AI распарсит и создаст IFC через BIM-пайплайн.
            </p>
            <textarea
              value={description}
              onChange={e => setDescription(e.target.value)}
              placeholder={"Пример:\nДвухэтажный жилой дом 12×10м, двускатная крыша.\n3 спальни, кухня, гостиная. Балкон на втором этаже."}
              style={{ width: "100%", height: 130, padding: 10, background: "var(--bg2)", border: "1px solid var(--border)", borderRadius: 8, color: "var(--text)", resize: "vertical", fontSize: "0.85rem", fontFamily: "inherit" }}
            />
            <button className="btn-gen" onClick={generateFromDesc} disabled={nlLoading || !description.trim()} style={{ marginTop: 10 }}>
              {nlLoading ? "⏳ Парсинг..." : "🤖 Сгенерировать из описания"}
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
          {selectedFile && (tab === "view" || tab === "create" || tab === "nl") ? (
            <ThreeViewer filename={selectedFile} />
          ) : (
            <div className="viewer-placeholder">
              <div style={{ fontSize: "3rem", opacity: 0.3 }}>🏗️</div>
              <div style={{ textAlign: "center", maxWidth: 300, lineHeight: 1.7 }}>
                {tab === "plan"
                  ? "Загрузи план — LLM извлечёт параметры, затем сгенерируй IFC"
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
