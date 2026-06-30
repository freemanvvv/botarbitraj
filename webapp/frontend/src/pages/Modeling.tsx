import { useState, useEffect } from "react";
import ThreeViewer from "../components/ThreeViewer";

const API = "http://localhost:8765";

interface Stats {
  walls: number;
  slabs: number;
  windows: number;
  doors: number;
  openings: number;
  storeys: number;
}

interface IFCFile {
  name: string;
  size_kb: number;
  created: number;
  url: string;
}

export default function Modeling() {
  const [params, setParams] = useState({
    name: "Building",
    length: 15,
    width: 12,
    height: 7,
    num_floors: 2,
    wall_thickness: 0.4,
    slab_thickness: 0.2,
    roof_type: "gable",
    add_internal_walls: true,
    add_windows: true,
    add_doors: true,
  });
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(false);
  const [nlLoading, setNlLoading] = useState(false);
  const [files, setFiles] = useState<IFCFile[]>([]);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [tab, setTab] = useState<"create" | "view" | "nl">("create");
  const [description, setDescription] = useState("");

  useEffect(() => {
    fetch(`${API}/api/model/existing`)
      .then((r) => r.json())
      .then((d) => {
        setFiles(d.files);
        if (d.files.length > 0) setSelectedFile(d.files[0].name);
      })
      .catch(() => {});
  }, []);

  const generate = async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API}/api/model/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(params),
      });
      const data = await res.json();
      setStats(data.stats);
      setSelectedFile(data.filename);
      const fres = await fetch(`${API}/api/model/existing`);
      setFiles((await fres.json()).files);
      setTab("view");
    } catch (e) {
      alert("Ошибка генерации");
    } finally {
      setLoading(false);
    }
  };

  const generateFromDescription = async () => {
    if (!description.trim()) return;
    setNlLoading(true);
    try {
      // BIM-пайплайн: текст → BuildingProgram → FloorPlan → IFC
      const res = await fetch(`${API}/api/model/bim-generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ description }),
      });
      const genData = await res.json();
      setStats(genData.stats);
      setSelectedFile(genData.filename);
      const fres = await fetch(`${API}/api/model/existing`);
      setFiles((await fres.json()).files);
      setTab("view");
    } catch (e) {
      alert("❌ BIM-генерация не удалась. Проверь LM Studio (должен быть Qwen3-14B).");
    } finally {
      setNlLoading(false);
    }
  };

  const updateParam = (key: string, value: any) => setParams((p) => ({ ...p, [key]: value }));
  const formatDate = (ts: number) => new Date(ts * 1000).toLocaleString().slice(0, -3);

  return (
    <div style={{ height: "calc(70vh + 40px)", display: "flex", flexDirection: "column" }}>
      <h2 style={{ marginBottom: 12, display: "flex", alignItems: "center", gap: 8 }}>
        🏗️ Моделирование
        <div className="toggle-group">
          <button className={`toggle-btn ${tab === "create" ? "active" : ""}`} onClick={() => setTab("create")}>Параметры</button>
          <button className={`toggle-btn ${tab === "nl" ? "active" : ""}`} onClick={() => setTab("nl")}>По описанию</button>
          <button className={`toggle-btn ${tab === "view" ? "active" : ""}`} onClick={() => setTab("view")}>Просмотр</button>
        </div>
      </h2>

      <div className="modeling-grid" style={{ flex: 1 }}>
        <div className="modeling-params">
          {tab === "create" && (
            <>
              <h3 style={{ marginBottom: 12, color: "var(--accent)" }}>Параметры здания</h3>
              <label>Название<input value={params.name} onChange={(e) => updateParam("name", e.target.value)} /></label>
              <label>Длина (м)<input type="number" value={params.length} onChange={(e) => updateParam("length", +e.target.value)} min={4} max={100} /></label>
              <label>Ширина (м)<input type="number" value={params.width} onChange={(e) => updateParam("width", +e.target.value)} min={4} max={100} /></label>
              <label>Высота этажа (м)<input type="number" value={+(params.height / params.num_floors).toFixed(1)} onChange={(e) => updateParam("height", +e.target.value * params.num_floors)} min={2.5} max={6} step={0.1} /></label>
              <label>Этажей<input type="number" value={params.num_floors} onChange={(e) => updateParam("num_floors", Math.max(1, +e.target.value))} min={1} max={10} /></label>
              <label>Крыша<select value={params.roof_type} onChange={(e) => updateParam("roof_type", e.target.value)}>
                <option value="gable">Двускатная</option>
                <option value="flat">Плоская</option>
              </select></label>
              <label><input type="checkbox" checked={params.add_windows} onChange={(e) => updateParam("add_windows", e.target.checked)} /> Окна</label>
              <label><input type="checkbox" checked={params.add_doors} onChange={(e) => updateParam("add_doors", e.target.checked)} /> Двери</label>
              <label><input type="checkbox" checked={params.add_internal_walls} onChange={(e) => updateParam("add_internal_walls", e.target.checked)} /> Перегородки</label>
              <button className="btn-gen" onClick={generate} disabled={loading}>
                {loading ? "⏳ Генерация..." : "🚀 Сгенерировать IFC"}
              </button>
            </>
          )}

          {tab === "nl" && (
            <>
              <h3 style={{ marginBottom: 12, color: "var(--accent)" }}>🏗️ Модель по описанию</h3>
              <p style={{ fontSize: "0.85rem", color: "var(--text2)", marginBottom: 12 }}>
                Опиши здание текстом — AI распарсит и создаст IFC.
              </p>
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder={'Пример:\nДвухэтажный дом 12 на 10 метров, с двускатной крышей. 3 спальни, кухня, гостиная на первом этаже.'}
                style={{ width: "100%", height: 120, padding: 10, background: "var(--surface2)", border: "1px solid var(--border)", borderRadius: 8, color: "var(--text)", resize: "vertical", fontSize: "0.9rem" }}
              />
              <button className="btn-gen" onClick={generateFromDescription} disabled={nlLoading || !description.trim()}>
                {nlLoading ? "⏳ Парсинг..." : "🤖 Сгенерировать из описания"}
              </button>
            </>
          )}

          {tab === "view" && (
            <>
              <h3 style={{ marginBottom: 12, color: "var(--accent)" }}>Готовые модели</h3>
              {files.length === 0 && <p style={{ color: "var(--text2)" }}>Нет моделей</p>}
              <div className="model-files">
                {files.map((f) => (
                  <div key={f.name} className="model-file" style={{ cursor: "pointer", border: selectedFile === f.name ? "1px solid var(--accent)" : "1px solid transparent" }}
                    onClick={() => setSelectedFile(f.name)}>
                    <span>{f.name}</span>
                    <span style={{ color: "var(--text2)", fontSize: "0.75rem" }}>{f.size_kb} KB · {formatDate(f.created)}</span>
                    <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                      <a href={f.url} download onClick={(e) => e.stopPropagation()} style={{ color: 'var(--accent)', cursor: 'pointer', fontSize: '0.9rem' }}>⬇️</a>
                      <span onClick={async (e) => {
                        e.stopPropagation();
                        if (confirm(`Удалить ${f.name}?`)) {
                          await fetch('http://localhost:8765/api/model/delete/' + f.name, { method: 'DELETE' });
                          const res = await fetch('http://localhost:8765/api/model/existing');
                          setFiles((await res.json()).files);
                          if (selectedFile === f.name) setSelectedFile(null);
                        }
                      }} style={{ color: 'var(--danger)', cursor: 'pointer', fontSize: '0.9rem' }}>🗑️</span>
                    </div>
                  </div>
                ))}
              </div>
              {stats && (
                <>
                  <h3 style={{ margin: "12px 0 8px", color: "var(--accent)" }}>Статистика</h3>
                  <div className="model-stats">
                    {Object.entries(stats).map(([k, v]) => (
                      <div key={k} className="model-stat">
                        <div className="stat-value">{v}</div>
                        <div className="stat-label">{k}</div>
                      </div>
                    ))}
                  </div>
                </>
              )}
              <p style={{ fontSize: "0.8rem", color: "var(--text2)", marginTop: 12 }}>
                🖱️ Вращение · 🖱️ ПКМ панорама · 🖱️ Колёсико зум
              </p>
            </>
          )}
        </div>

        <div className="modeling-viewer">
          {tab === "view" && selectedFile ? (
            <ThreeViewer filename={selectedFile} />
          ) : (
            <div className="viewer-placeholder">
              <div style={{ textAlign: "center" }}>
                <div style={{ fontSize: "3rem", marginBottom: 12 }}>🏗️</div>
                <div>{tab === "create" ? "Задай параметры и нажми «Сгенерировать»" : "Напиши описание здания"}</div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
