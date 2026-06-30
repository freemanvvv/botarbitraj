import { useState } from "react";
import Archive from "./pages/Archive";
import ChatTab from "./pages/ChatTab";
import Modeling from "./pages/Modeling";
import "./App.css";

type Tab = "archive" | "chat" | "modeling";

const TABS: { key: Tab; label: string; icon: string }[] = [
  { key: "archive", label: "Архив", icon: "📚" },
  { key: "chat", label: "Чат с ботом", icon: "💬" },
  { key: "modeling", label: "Моделирование", icon: "🏗️" },
];

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>("archive");

  return (
    <div className="app-container">
      <header className="app-header">
        <h1 className="app-title">⚡ Construction AI Copilot</h1>
        <div className="tab-bar">
          {TABS.map((t) => (
            <button
              key={t.key}
              className={`tab-btn ${activeTab === t.key ? "active" : ""}`}
              onClick={() => setActiveTab(t.key)}
            >
              <span className="tab-icon">{t.icon}</span>
              <span className="tab-label">{t.label}</span>
            </button>
          ))}
        </div>
      </header>
      <main className="app-main">
        {activeTab === "archive" && <Archive />}
        {activeTab === "chat" && <ChatTab />}
        {activeTab === "modeling" && <Modeling />}
      </main>
    </div>
  );
}
