/**
 * App.jsx — Root component. Defines the top-level layout.
 *
 * LAYOUT:
 *   ┌──────────────┬──────────────────────────────────┐
 *   │   Sidebar    │         Main Content Area        │
 *   │  (nav links) │  (changes based on active page)  │
 *   └──────────────┴──────────────────────────────────┘
 *
 * CONCEPT — useState:
 *   `useState` is a React "hook" — a function that lets a component have memory.
 *   `const [activePage, setActivePage] = useState("jobs")` means:
 *   - `activePage` is the current value (starts as "jobs")
 *   - `setActivePage("applications")` updates it and re-renders the component
 *   Without state, clicking nav links would do nothing — there'd be no memory of
 *   which page was clicked.
 *
 * CONCEPT — Conditional rendering:
 *   React doesn't have `if` blocks in JSX — instead we use ternary expressions
 *   or short-circuit evaluation (&&) to conditionally render components.
 */

import { useState } from "react";
import { Sidebar } from "./components/Sidebar";
import { ApplicationsPage } from "./pages/ApplicationsPage";
import { JobsPage } from "./pages/JobsPage";
import { OrchestratorPage } from "./pages/OrchestratorPage";
import { SettingsPage } from "./pages/SettingsPage";

function App() {
  const [activePage, setActivePage] = useState("jobs");

  // ── Orchestrator session state (lifted here so it survives tab switches) ────
  // CONCEPT — Lifting state up:
  //   React destroys a component's local state when it unmounts (navigating away).
  //   By keeping sessionId and sessionData here in App — which never unmounts —
  //   the orchestrator session stays alive while you browse other tabs.
  //   OrchestratorPage receives these as props and resumes polling on remount.
  const [orchSessionId, setOrchSessionId] = useState(null);
  const [orchSessionData, setOrchSessionData] = useState(null);
  const [orchDryRun, setOrchDryRun] = useState(false);
  const [orchMode, setOrchMode] = useState("fresh_scan");
  const [orchHandoff, setOrchHandoff] = useState(false);
  const [orchMaxApply, setOrchMaxApply] = useState(5);

  return (
    // bg-[var(--bg-base)] is set in body via index.css; this div just provides flex layout.
    <div className="flex min-h-screen">
      <Sidebar activePage={activePage} onNavigate={setActivePage} />
      <main className="flex-1 min-h-screen overflow-auto">
        {activePage === "jobs"         && <JobsPage />}
        {activePage === "applications" && <ApplicationsPage />}
        {activePage === "orchestrator" && (
          <OrchestratorPage
            sessionId={orchSessionId}
            setSessionId={setOrchSessionId}
            sessionData={orchSessionData}
            setSessionData={setOrchSessionData}
            dryRun={orchDryRun}
            setDryRun={setOrchDryRun}
            mode={orchMode}
            setMode={setOrchMode}
            handoff={orchHandoff}
            setHandoff={setOrchHandoff}
            maxApply={orchMaxApply}
            setMaxApply={setOrchMaxApply}
          />
        )}
        {activePage === "settings" && <SettingsPage />}
        {!["jobs", "applications", "orchestrator", "settings"].includes(activePage) && (
          <div className="p-8 font-mono text-xs" style={{ color: 'var(--text-2)' }}>
            Page not found.
          </div>
        )}
      </main>
    </div>
  );
}

export default App;
