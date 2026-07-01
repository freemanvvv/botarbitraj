import { useState } from "react";
import type { JSX } from "react";
import Archive from "./pages/Archive";
import ChatTab from "./pages/ChatTab";
import Modeling from "./pages/Modeling";
import GsplatTab from "./pages/GsplatTab";
import Estimate from "./pages/Estimate";
import "./App.css";

type Tab = "archive" | "chat" | "modeling" | "gsplat" | "estimate";

const IconArchive = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M4 4h16v4H4z" />
    <path d="M4 8v12a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1V8" />
    <path d="M9 13h6" />
  </svg>
);

const IconChat = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <rect x="3" y="7" width="18" height="12" rx="3" />
    <circle cx="8.5" cy="13" r="1" fill="currentColor" stroke="none" />
    <circle cx="12" cy="13" r="1" fill="currentColor" stroke="none" />
    <circle cx="15.5" cy="13" r="1" fill="currentColor" stroke="none" />
    <path d="M8 7V5a4 4 0 0 1 8 0v2" />
  </svg>
);

const IconModeling = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 3L21 7.5v9L12 21 3 16.5v-9L12 3z" />
    <path d="M12 3v18" />
    <path d="M3 7.5l9 4.5 9-4.5" />
  </svg>
);

const IconGsplat = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="3" />
    <circle cx="4"  cy="8"  r="1.5" />
    <circle cx="20" cy="8"  r="1.5" />
    <circle cx="4"  cy="16" r="1.5" />
    <circle cx="20" cy="16" r="1.5" />
    <circle cx="12" cy="3"  r="1.5" />
    <circle cx="12" cy="21" r="1.5" />
    <path d="M4 8l8 4M20 8l-8 4M4 16l8-4M20 16l-8-4M12 3v6M12 21v-6" />
  </svg>
);

const IconEstimate = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <rect x="5" y="3" width="14" height="18" rx="2" />
    <path d="M9 7h6M9 11h6M9 15h3" />
  </svg>
);

const TABS: { key: Tab; label: string; Icon: () => JSX.Element }[] = [
  { key: "archive",  label: "Архив",          Icon: IconArchive  },
  { key: "chat",     label: "Чат с ботом",    Icon: IconChat     },
  { key: "modeling", label: "Моделирование",  Icon: IconModeling },
  { key: "estimate", label: "Сметы",          Icon: IconEstimate },
  { key: "gsplat",   label: "3D-карты",       Icon: IconGsplat   },
];

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>("archive");

  return (
    <div className="app-container">
      <header className="app-header">
        <div className="header-top">
          <h1 className="app-title">Construction AI Copilot</h1>
        </div>
        <nav className="tab-bar">
          {TABS.map((t) => (
            <button
              key={t.key}
              className={`tab-btn ${activeTab === t.key ? "active" : ""}`}
              onClick={() => setActiveTab(t.key)}
            >
              <span className="tab-icon"><t.Icon /></span>
              <span className="tab-label">{t.label}</span>
            </button>
          ))}
        </nav>
      </header>
      <main className="app-main">
        {activeTab === "archive"  && <Archive />}
        {activeTab === "chat"     && <ChatTab />}
        {activeTab === "modeling" && <Modeling />}
        {activeTab === "estimate" && <Estimate />}
        {activeTab === "gsplat"   && <GsplatTab />}
      </main>
    </div>
  );
}
