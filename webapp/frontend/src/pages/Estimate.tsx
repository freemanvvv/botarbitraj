import { useState, useEffect } from "react";

const API = "http://localhost:8765";

interface EstimateItem {
  type: "material" | "work";
  name: string;
  unit: string;
  quantity: number;
  unit_price: number;
  total: number;
  note?: string;
}

interface EstimateResult {
  estimate_id: number;
  project_name: string;
  items: EstimateItem[];
  total_materials: number;
  total_work: number;
  total: number;
  raw_llm_response?: string;
}

interface PriceEntry {
  id: number;
  name: string;
  unit: string;
  price: number;
  category: string;
  region: string;
}

type SubTab = "generate" | "pricing";

const fmt = (n: number) => n.toLocaleString("ru-RU", { maximumFractionDigits: 2 });

export default function Estimate() {
  const [subTab, setSubTab] = useState<SubTab>("generate");

  // ── Генерация сметы ──
  const [description, setDescription] = useState("");
  const [models, setModels] = useState<string[]>(["local-model"]);
  const [model, setModel] = useState("local-model");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<EstimateResult | null>(null);

  // ── Расценки ──
  const [materials, setMaterials] = useState<PriceEntry[]>([]);
  const [work, setWork] = useState<PriceEntry[]>([]);
  const [pricingQuery, setPricingQuery] = useState("");
  const [newEntry, setNewEntry] = useState({ kind: "material" as "material" | "work", name: "", unit: "", price: "", category: "" });
  const [addError, setAddError] = useState("");

  useEffect(() => {
    fetch(`${API}/api/chat/models`)
      .then(r => r.json())
      .then(d => { if (d.models?.length) { setModels(d.models); setModel(d.models[0]); } })
      .catch(() => {});
  }, []);

  const loadPricing = async (q: string = "") => {
    const qs = q ? `?q=${encodeURIComponent(q)}` : "";
    const [m, w] = await Promise.all([
      fetch(`${API}/api/pricing/materials${qs}`).then(r => r.json()),
      fetch(`${API}/api/pricing/work${qs}`).then(r => r.json()),
    ]);
    setMaterials(m.materials || []);
    setWork(w.work || []);
  };

  useEffect(() => { if (subTab === "pricing") loadPricing(); }, [subTab]);

  const generate = async () => {
    if (!description.trim()) return;
    setLoading(true);
    setError("");
    setResult(null);
    try {
      const res = await fetch(`${API}/api/estimate/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ description, model }),
      });
      const d = await res.json();
      if (!res.ok) throw new Error(d.detail || "Ошибка расчёта сметы");
      setResult(d);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const addPriceEntry = async () => {
    setAddError("");
    const price = parseFloat(newEntry.price);
    if (!newEntry.name.trim() || !newEntry.unit.trim() || isNaN(price) || price < 0) {
      setAddError("Заполни название, единицу измерения и неотрицательную цену");
      return;
    }
    try {
      const endpoint = newEntry.kind === "material" ? "materials" : "work";
      const res = await fetch(`${API}/api/pricing/${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: newEntry.name, unit: newEntry.unit, price, category: newEntry.category }),
      });
      const d = await res.json();
      if (!res.ok) throw new Error(JSON.stringify(d.detail || d));
      setNewEntry({ kind: newEntry.kind, name: "", unit: "", price: "", category: "" });
      await loadPricing(pricingQuery);
    } catch (e: any) {
      setAddError(e.message);
    }
  };

  const inputStyle: React.CSSProperties = {
    width: "100%", padding: "8px 10px", background: "var(--bg2)", border: "1px solid var(--border)",
    borderRadius: 8, color: "var(--text)", fontSize: "0.83rem", fontFamily: "inherit",
  };

  return (
    <div>
      <div style={{ marginBottom: 14, display: "flex", alignItems: "center", gap: 10 }}>
        <h2>💰 Сметы</h2>
        <div className="toggle-group">
          {([["generate", "Рассчитать"], ["pricing", "📋 Расценки"]] as [SubTab, string][]).map(([k, l]) => (
            <button key={k} className={`toggle-btn ${subTab === k ? "active" : ""}`} onClick={() => setSubTab(k)}>{l}</button>
          ))}
        </div>
      </div>

      {subTab === "generate" && (
        <div style={{ display: "grid", gridTemplateColumns: "360px 1fr", gap: 20 }}>
          <div>
            <label style={{ display: "block", marginBottom: 6, fontSize: "0.78rem", color: "var(--text2)", textTransform: "uppercase", letterSpacing: "0.06em" }}>
              Локальная модель ИИ
            </label>
            <select value={model} onChange={e => setModel(e.target.value)} disabled={loading} style={{ ...inputStyle, marginBottom: 10 }}>
              {models.map(m => <option key={m} value={m}>{m}</option>)}
            </select>

            <p style={{ fontSize: "0.78rem", color: "var(--text2)", marginBottom: 8, lineHeight: 1.6 }}>
              Опиши объект — LLM составит ведомость работ/материалов, а стоимость посчитает код
              по базе расценок (не LLM — арифметика всегда точная).
            </p>

            <textarea
              value={description}
              onChange={e => setDescription(e.target.value)}
              disabled={loading}
              placeholder={"Пример:\nДвухэтажный жилой дом 12×10м, кирпичные стены, фундамент ленточный, двускатная крыша с шифером."}
              style={{ ...inputStyle, height: 140, resize: "vertical" }}
            />

            {error && (
              <div style={{ padding: "8px 12px", marginTop: 10, background: "rgba(255,69,58,0.1)", border: "1px solid rgba(255,69,58,0.25)", borderRadius: 8, fontSize: "0.78rem", color: "var(--danger)" }}>
                ❌ {error}
              </div>
            )}

            <button className="btn-gen" onClick={generate} disabled={loading || !description.trim()} style={{ marginTop: 10 }}>
              {loading ? "⏳ Считаю..." : "💰 Рассчитать смету"}
            </button>
          </div>

          <div>
            {!result && !loading && (
              <p style={{ color: "var(--text2)", fontSize: "0.85rem" }}>Опиши объект слева и нажми «Рассчитать смету».</p>
            )}
            {result && (
              <>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.82rem" }}>
                  <thead>
                    <tr style={{ borderBottom: "1px solid var(--border)", textAlign: "left" }}>
                      <th style={{ padding: "6px 8px" }}>Наименование</th>
                      <th style={{ padding: "6px 8px" }}>Ед.</th>
                      <th style={{ padding: "6px 8px", textAlign: "right" }}>Кол-во</th>
                      <th style={{ padding: "6px 8px", textAlign: "right" }}>Цена</th>
                      <th style={{ padding: "6px 8px", textAlign: "right" }}>Сумма</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.items.map((it, i) => (
                      <tr key={i} style={{ borderBottom: "1px solid var(--border)" }}>
                        <td style={{ padding: "6px 8px" }}>
                          {it.name}
                          {it.note && <div style={{ fontSize: "0.7rem", color: "var(--danger)" }}>{it.note}</div>}
                        </td>
                        <td style={{ padding: "6px 8px", color: "var(--text2)" }}>{it.unit}</td>
                        <td style={{ padding: "6px 8px", textAlign: "right" }}>{fmt(it.quantity)}</td>
                        <td style={{ padding: "6px 8px", textAlign: "right" }}>{fmt(it.unit_price)}</td>
                        <td style={{ padding: "6px 8px", textAlign: "right", fontWeight: 600 }}>{fmt(it.total)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>

                <div style={{ marginTop: 12, padding: "10px 14px", background: "var(--bg2)", borderRadius: 8, border: "1px solid var(--border)" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.82rem", color: "var(--text2)", marginBottom: 4 }}>
                    <span>Материалы</span><span>{fmt(result.total_materials)} сум</span>
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.82rem", color: "var(--text2)", marginBottom: 8 }}>
                    <span>Работы</span><span>{fmt(result.total_work)} сум</span>
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: "1rem", fontWeight: 700, borderTop: "1px solid var(--border)", paddingTop: 8 }}>
                    <span>ВСЕГО</span><span>{fmt(result.total)} сум</span>
                  </div>
                </div>

                <div style={{ display: "flex", gap: 10, marginTop: 12 }}>
                  <a className="btn-gen" style={{ textDecoration: "none", textAlign: "center" }}
                     href={`${API}/api/estimate/${result.estimate_id}/export/xlsx`} target="_blank" rel="noreferrer">
                    📄 Скачать XLSX
                  </a>
                  <a className="btn-gen" style={{ textDecoration: "none", textAlign: "center" }}
                     href={`${API}/api/estimate/${result.estimate_id}/export/pdf`} target="_blank" rel="noreferrer">
                    📄 Скачать PDF
                  </a>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {subTab === "pricing" && (
        <div>
          <div style={{ display: "flex", gap: 10, marginBottom: 14 }}>
            <input
              style={{ ...inputStyle, maxWidth: 300 }}
              placeholder="Поиск по названию..."
              value={pricingQuery}
              onChange={e => { setPricingQuery(e.target.value); loadPricing(e.target.value); }}
            />
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 }}>
            <div>
              <h3 style={{ marginBottom: 8 }}>Материалы ({materials.length})</h3>
              <div style={{ maxHeight: 360, overflowY: "auto", border: "1px solid var(--border)", borderRadius: 8 }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.78rem" }}>
                  <tbody>
                    {materials.map(m => (
                      <tr key={m.id} style={{ borderBottom: "1px solid var(--border)" }}>
                        <td style={{ padding: "5px 8px" }}>{m.name}</td>
                        <td style={{ padding: "5px 8px", color: "var(--text2)" }}>{m.unit}</td>
                        <td style={{ padding: "5px 8px", textAlign: "right", fontWeight: 600 }}>{fmt(m.price)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
            <div>
              <h3 style={{ marginBottom: 8 }}>Работы ({work.length})</h3>
              <div style={{ maxHeight: 360, overflowY: "auto", border: "1px solid var(--border)", borderRadius: 8 }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.78rem" }}>
                  <tbody>
                    {work.map(w => (
                      <tr key={w.id} style={{ borderBottom: "1px solid var(--border)" }}>
                        <td style={{ padding: "5px 8px" }}>{w.name}</td>
                        <td style={{ padding: "5px 8px", color: "var(--text2)" }}>{w.unit}</td>
                        <td style={{ padding: "5px 8px", textAlign: "right", fontWeight: 600 }}>{fmt(w.price)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          <h3 style={{ margin: "16px 0 8px" }}>Добавить позицию</h3>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <select value={newEntry.kind} onChange={e => setNewEntry({ ...newEntry, kind: e.target.value as "material" | "work" })} style={{ ...inputStyle, width: 120 }}>
              <option value="material">Материал</option>
              <option value="work">Работа</option>
            </select>
            <input style={{ ...inputStyle, width: 220 }} placeholder="Название"
                   value={newEntry.name} onChange={e => setNewEntry({ ...newEntry, name: e.target.value })} />
            <input style={{ ...inputStyle, width: 100 }} placeholder="Ед. изм."
                   value={newEntry.unit} onChange={e => setNewEntry({ ...newEntry, unit: e.target.value })} />
            <input style={{ ...inputStyle, width: 130 }} placeholder="Цена, сум" type="number" min={0}
                   value={newEntry.price} onChange={e => setNewEntry({ ...newEntry, price: e.target.value })} />
            <input style={{ ...inputStyle, width: 140 }} placeholder="Категория"
                   value={newEntry.category} onChange={e => setNewEntry({ ...newEntry, category: e.target.value })} />
            <button className="btn-gen" style={{ width: "auto", padding: "8px 16px" }} onClick={addPriceEntry}>Добавить</button>
          </div>
          {addError && <div style={{ marginTop: 8, fontSize: "0.78rem", color: "var(--danger)" }}>❌ {addError}</div>}
        </div>
      )}
    </div>
  );
}
