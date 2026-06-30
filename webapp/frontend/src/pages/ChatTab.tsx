import { useState, useEffect, useRef } from "react";

interface Message {
  role: "user" | "bot" | "system";
  content: string;
}

const API = "http://localhost:8765";

export default function ChatTab() {
  const [messages, setMessages] = useState<Message[]>(() => {
    try {
      const saved = localStorage.getItem('copilot_chat_messages');
      if (saved) return JSON.parse(saved);
    } catch {}
    return [
      { role: "system", content: "Construction AI Copilot ⚡ — задавай вопросы по строительным нормам Узбекистана." },
    ];
  });
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [models, setModels] = useState<string[]>(["qwen/qwen3-14b"]);
  const [selectedModel, setSelectedModel] = useState("qwen/qwen3-14b");
  const [useRag, setUseRag] = useState(true);
  const [ragStatus, setRagStatus] = useState<Record<string, number>>({});
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetch(`${API}/api/chat/models`)
      .then((r) => r.json())
      .then((d) => {
        if (d.models?.length) setModels(d.models);
      })
      .catch(() => {});

    fetch(`${API}/api/chat/status`)
      .then((r) => r.json())
      .then((d) => {
        if (d.collections) setRagStatus(d.collections);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    localStorage.setItem('copilot_chat_messages', JSON.stringify(messages));
  }, [messages]);

  const sendMessage = async () => {
    if (!input.trim() || loading) return;

    const userMsg = input.trim();
    setInput("");
    setMessages((prev) => [...prev, { role: "user", content: userMsg }]);
    setLoading(true);

    try {
      const res = await fetch(`${API}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: userMsg, model: selectedModel, use_rag: useRag }),
      });
      const data = await res.json();
      setMessages((prev) => [
        ...prev,
        { role: "bot", content: data.response },
        ...(data.rag_used ? [] : [{ role: "system" as const, content: "ℹ️ RAG отключён — ответ без нормативной базы." }]),
      ]);
    } catch (e) {
      setMessages((prev) => [...prev, { role: "bot", content: "❌ Ошибка: не удалось получить ответ. Проверь LM Studio." }]);
    } finally {
      setLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  return (
    <div>
      <h2 style={{ marginBottom: 12, display: "flex", alignItems: "center", gap: 8 }}>
        💬 Чат с ботом
        <span style={{ fontSize: "0.8rem", color: "var(--text2)", fontWeight: 400 }}>
          {Object.entries(ragStatus).map(([k, v]) => `${k}: ${v.toLocaleString()} чанков`).join(" · ")}
        </span>
      </h2>

      <div className="chat-settings">
        <label>
          Модель:
          <select value={selectedModel} onChange={(e) => setSelectedModel(e.target.value)}>
            {models.map((m) => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
        </label>
        <label>
          <input type="checkbox" checked={useRag} onChange={(e) => setUseRag(e.target.checked)} />
          RAG
        </label>
        <button onClick={() => {
          if (confirm('Очистить историю чата?')) {
            setMessages([{ role: 'system', content: 'Construction AI Copilot ⚡ — задавай вопросы по строительным нормам Узбекистана.' }]);
            localStorage.removeItem('copilot_chat_messages');
          }
        }} style={{
          padding: '4px 10px', background: 'var(--surface2)', border: 'none',
          borderRadius: 6, color: 'var(--danger)', cursor: 'pointer', fontSize: '0.8rem',
          marginLeft: 'auto'
        }}>
          🗑️ Очистить
        </button>
      </div>

      <div className="chat-container">
        <div className="chat-messages">
          {messages.map((m, i) => (
            <div key={i} className={`msg ${m.role}`}>{m.content}</div>
          ))}
          {loading && <div className="msg bot" style={{ fontStyle: "italic" }}>⏳ Генерация...</div>}
          <div ref={messagesEndRef} />
        </div>
        <div className="chat-input">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Спроси про строительные нормы..."
            rows={2}
          />
          <button onClick={sendMessage} disabled={loading || !input.trim()}>
            {loading ? "..." : "→"}
          </button>
        </div>
      </div>
    </div>
  );
}
