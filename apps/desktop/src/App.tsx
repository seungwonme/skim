import { useEffect, useState } from "react";
import {
  LuChartColumnBig,
  LuDatabase,
  LuKeyRound,
  LuPanelsTopLeft,
  LuRefreshCw,
  LuSearch,
} from "react-icons/lu";

import "./App.css";
import { CredentialsPanel } from "./components/CredentialsPanel";
import { ExplorerPanel } from "./components/ExplorerPanel";
import { SourcesPanel } from "./components/SourcesPanel";
import { getAppOverview, listCredentials, listTrackedSources } from "./lib/api";
import type { AppOverview, PlatformCredential, TrackedSource } from "./lib/types";

type TabId = "sources" | "credentials" | "explorer";

const tabs: Array<{
  id: TabId;
  label: string;
  description: string;
  icon: typeof LuPanelsTopLeft;
}> = [
  { id: "sources", label: "Sources", description: "수집 대상", icon: LuPanelsTopLeft },
  { id: "credentials", label: "Credentials", description: "Keychain", icon: LuKeyRound },
  { id: "explorer", label: "Explorer", description: "검색 / export", icon: LuSearch },
];

export default function App() {
  const [activeTab, setActiveTab] = useState<TabId>("sources");
  const [overview, setOverview] = useState<AppOverview | null>(null);
  const [sources, setSources] = useState<TrackedSource[]>([]);
  const [credentials, setCredentials] = useState<PlatformCredential[]>([]);
  const [status, setStatus] = useState<{
    tone: "neutral" | "success" | "error";
    message: string;
  }>({
    tone: "neutral",
    message: "desktop MVP를 로드하는 중입니다.",
  });

  async function refreshAll() {
    try {
      const [nextOverview, nextSources, nextCredentials] = await Promise.all([
        getAppOverview(),
        listTrackedSources(),
        listCredentials(),
      ]);
      setOverview(nextOverview);
      setSources(nextSources);
      setCredentials(nextCredentials);
    } catch (error) {
      setStatus({
        tone: "error",
        message: `앱 초기화 실패: ${String(error)}`,
      });
    }
  }

  useEffect(() => {
    void refreshAll();
  }, []);

  function report(message: string, tone: "neutral" | "success" | "error" = "neutral") {
    setStatus({ message, tone });
  }

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand-block">
          <p className="eyebrow">Skim Desktop</p>
          <h1>Read Your Signal Archive</h1>
          <p className="lede">
            따뜻한 로컬 작업 공간에서 소스를 정리하고, 자격 증명을 관리하고, 저장된 포스트를
            천천히 탐색합니다.
          </p>
        </div>

        <nav className="tab-list" aria-label="Primary">
          {tabs.map((tab) => (
            <button className={`tab-button ${activeTab === tab.id ? "active" : ""}`} key={tab.id} type="button" onClick={() => setActiveTab(tab.id)}>
              <span className="tab-icon">
                <tab.icon />
              </span>
              <span className="tab-label">
                <strong>{tab.label}</strong>
                <span>{tab.description}</span>
              </span>
            </button>
          ))}
        </nav>
      </aside>

      <section className="main-stage">
        <header className="status-bar">
          <div>
            <p className="eyebrow">Status</p>
            <p className={`status-message ${status.tone}`}>{status.message}</p>
          </div>
          <button className="ghost-button icon-button" type="button" onClick={() => void refreshAll()}>
            <LuRefreshCw />
            <span>새로고침</span>
          </button>
        </header>

        <section className="overview-grid" aria-label="Workspace overview">
          <article className="overview-card">
            <span className="overview-icon">
              <LuDatabase />
            </span>
            <div>
              <p className="eyebrow">Posts</p>
              <strong>{overview?.postsCount ?? "-"}</strong>
            </div>
          </article>
          <article className="overview-card">
            <span className="overview-icon">
              <LuPanelsTopLeft />
            </span>
            <div>
              <p className="eyebrow">Sources</p>
              <strong>{overview?.trackedSourcesCount ?? "-"}</strong>
            </div>
          </article>
          <article className="overview-card">
            <span className="overview-icon">
              <LuKeyRound />
            </span>
            <div>
              <p className="eyebrow">Credentials</p>
              <strong>{overview?.credentialsCount ?? "-"}</strong>
            </div>
          </article>
          <article className="overview-card wide tone-dark">
            <span className="overview-icon">
              <LuChartColumnBig />
            </span>
            <div>
              <p className="eyebrow">Database Path</p>
              <strong className="path-copy">{overview?.dbPath ?? "-"}</strong>
            </div>
          </article>
        </section>

        {activeTab === "sources" ? (
          <SourcesPanel report={report} sources={sources} onChanged={refreshAll} />
        ) : null}
        {activeTab === "credentials" ? (
          <CredentialsPanel report={report} credentials={credentials} onChanged={refreshAll} />
        ) : null}
        {activeTab === "explorer" ? <ExplorerPanel report={report} /> : null}
      </section>
    </main>
  );
}
