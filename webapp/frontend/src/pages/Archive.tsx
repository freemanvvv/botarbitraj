import { useState, useEffect } from "react";

interface Doc {
  id: string;
  doc_type: string;
  number: string;
  year: string;
  title: string;
  language: string;
  status: string;
  source_url: string;
}

interface Group {
  type: string;
  count: number;
}

const API = "http://localhost:8765";

export default function Archive() {
  const [groups, setGroups] = useState<Group[]>([]);
  const [docs, setDocs] = useState<Doc[]>([]);
  const [total, setTotal] = useState(0);
  const [totalPages, setTotalPages] = useState(0);
  const [page, setPage] = useState(1);
  const [filterType, setFilterType] = useState("");
  const [filterStatus, setFilterStatus] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState<Doc & { text_preview: string; chunks: number } | null>(null);
  const [viewMode, setViewMode] = useState<"grid" | "list">("grid");

  useEffect(() => {
    fetch(`${API}/api/archive/groups`).then((r) => r.json()).then((d) => setGroups(d.groups));
  }, []);

  // Дебаунс: применяем поисковый запрос через 300мс после последнего нажатия
  useEffect(() => {
    const timer = setTimeout(() => {
      setSearch(searchInput);
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [searchInput]);

  useEffect(() => {
    setLoading(true);
    const params = new URLSearchParams({ page: String(page), page_size: "24" });
    if (filterType) params.set("doc_type", filterType);
    if (filterStatus) params.set("status", filterStatus);
    if (search) params.set("search", search);

    let cancelled = false;
    fetch(`${API}/api/archive/docs?${params}`)
      .then((r) => r.json())
      .then((d) => {
        if (!cancelled) {
          setDocs(d.items);
          setTotal(d.total);
          setTotalPages(d.total_pages);
          setLoading(false);
        }
      })
      .catch(() => { if (!cancelled) setLoading(false); });

    return () => { cancelled = true; };
  }, [page, filterType, filterStatus, search]);

  const openDetail = async (docId: string) => {
    const res = await fetch(`${API}/api/archive/doc/${docId}`);
    const data = await res.json();
    setDetail({ ...data.doc, text_preview: data.text_preview, chunks: data.chunks });
  };

  return (
    <div>
      <h2 style={{ marginBottom: 12, display: "flex", alignItems: "center", gap: 8 }}>
        📚 Архив нормативов
        <span style={{ fontSize: "0.85rem", color: "var(--text2)", fontWeight: 400 }}>
          {total} документов
        </span>
      </h2>

      <div className="search-bar">
        <input
          placeholder="Поиск по названию, номеру или ID..."
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
        />
        <div className="toggle-group">
          <button className={`toggle-btn ${viewMode === "grid" ? "active" : ""}`} onClick={() => setViewMode("grid")}>Сетка</button>
          <button className={`toggle-btn ${viewMode === "list" ? "active" : ""}`} onClick={() => setViewMode("list")}>Список</button>
        </div>
      </div>

      <div className="filter-bar">
        <select value={filterType} onChange={(e) => { setFilterType(e.target.value); setPage(1); }}>
          <option value="">Все типы</option>
          {groups.map((g) => (
            <option key={g.type} value={g.type}>{g.type} ({g.count})</option>
          ))}
        </select>
        <select value={filterStatus} onChange={(e) => { setFilterStatus(e.target.value); setPage(1); }}>
          <option value="">Все статусы</option>
          <option value="active">Действующие</option>
          <option value="superseded">Отменённые</option>
          <option value="unknown">Неизвестно</option>
        </select>
      </div>

      {loading && <div className="loading"><div className="spinner" /> Загрузка...</div>}

      {!loading && (
        <>
          <div className={viewMode === "grid" ? "card-grid" : ""} style={viewMode === "list" ? { display: "flex", flexDirection: "column", gap: 6 } : {}}>
            {docs.map((doc) => (
              <div key={doc.id} className={viewMode === "grid" ? "card" : "card"} style={viewMode === "list" ? { display: "flex", alignItems: "center", gap: 12, padding: 10 } : {}} onClick={() => openDetail(doc.id)}>
                <div>
                  <div className="card-title">{doc.doc_type} {doc.number}</div>
                  <div className="card-subtitle">{doc.title}</div>
                  {viewMode === "grid" && (
                    <div style={{ marginTop: 6, display: "flex", gap: 8, alignItems: "center" }}>
                      <span className={`card-badge badge-${doc.status}`}>{doc.status}</span>
                      <span style={{ color: "var(--text2)", fontSize: "0.75rem" }}>{doc.year} · {doc.language}</span>
                    </div>
                  )}
                </div>
                {viewMode === "list" && (
                  <div style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center" }}>
                    <span className={`card-badge badge-${doc.status}`}>{doc.status}</span>
                    <span style={{ color: "var(--text2)", fontSize: "0.8rem" }}>{doc.year}</span>
                  </div>
                )}
              </div>
            ))}
          </div>

          {totalPages > 1 && (
            <div className="pagination">
              <button disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>←</button>
              {Array.from({ length: Math.min(totalPages, 7) }, (_, i) => {
                let p = i + 1;
                if (totalPages > 7 && page > 4) p = page - 4 + i;
                if (p > totalPages) return null;
                return (
                  <button key={p} className={page === p ? "active" : ""} onClick={() => setPage(p)}>
                    {p}
                  </button>
                );
              })}
              <button disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}>→</button>
            </div>
          )}
        </>
      )}

      {/* Modal */}
      {detail && (
        <div className="modal-overlay" onClick={() => setDetail(null)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <button className="modal-close" onClick={() => setDetail(null)}>✕</button>
            <h3 style={{ marginBottom: 8 }}>{detail.doc_type} {detail.number} — {detail.title}</h3>
            <table style={{ width: "100%", fontSize: "0.9rem", borderCollapse: "collapse" }}>
              <tbody>
                {[["ID", detail.id], ["Тип", detail.doc_type], ["Номер", detail.number], ["Год", detail.year], ["Язык", detail.language], ["Статус", detail.status], ["Заменён на", detail.superseded_by], ["Чанков в БД", detail.chunks]].map(([k, v]) => (
                  v ? <tr key={k}><td style={{ padding: "4px 8px", color: "var(--text2)", width: 120 }}>{k}</td><td style={{ padding: "4px 8px" }}>{v}</td></tr> : null
                ))}
              </tbody>
            </table>
            {detail.source_url && (
              <p style={{ marginTop: 8 }}>
                <a href={detail.source_url} target="_blank" rel="noreferrer" style={{ color: "var(--accent)" }}>
                  🔗 Открыть источник
                </a>
              </p>
            )}
            {detail.text_preview && (
              <details style={{ marginTop: 12 }} open>
                <summary style={{ cursor: "pointer", color: "var(--accent)", marginBottom: 8 }}>Текст норматива</summary>
                <pre style={{ background: "var(--surface2)", padding: 12, borderRadius: 8, fontSize: "0.8rem", maxHeight: 300, overflow: "auto", whiteSpace: "pre-wrap" }}>
                  {detail.text_preview}
                </pre>
              </details>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
