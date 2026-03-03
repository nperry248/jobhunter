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
import { JobsPage } from "./pages/JobsPage";
import { ApplicationsPage } from "./pages/ApplicationsPage";
import { SettingsPage } from "./pages/SettingsPage";

// Maps page IDs to their component. Add new pages here.
// WHY a map instead of if/else: cleaner, easier to extend, no long chains of conditions.
const PAGES = {
  jobs: <JobsPage />,
  applications: <ApplicationsPage />,
  settings: <SettingsPage />,
};

function App() {
  // activePage controls which page component is shown in the main content area.
  // "jobs" is the default — what you see when you first open the app.
  const [activePage, setActivePage] = useState("jobs");

  return (
    <div className="flex min-h-screen bg-[#0a0a0a]">
      <Sidebar activePage={activePage} onNavigate={setActivePage} />
      <main className="flex-1 min-h-screen">
        {PAGES[activePage] ?? (
          <div className="p-8 text-zinc-500 text-sm">Page not found.</div>
        )}
      </main>
    </div>
  );
}

export default App;
