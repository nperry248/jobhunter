/**
 * Sidebar.jsx — The left navigation sidebar.
 *
 * CONCEPT — React components:
 *   A React component is a JavaScript function that returns JSX (HTML-like syntax).
 *   Components are reusable: you can use <Sidebar /> anywhere in your app and it
 *   renders the same UI. Props (properties) let you pass data into components.
 *
 * CONCEPT — JSX:
 *   JSX looks like HTML but it's actually JavaScript. `className` instead of `class`,
 *   `onClick` instead of `onclick`. React transpiles it to `React.createElement()` calls.
 *
 * CONCEPT — Tailwind classes:
 *   Instead of writing CSS files, we apply utility classes directly to elements.
 *   `flex flex-col` = `display: flex; flex-direction: column;`
 *   This approach is called "utility-first CSS".
 */

// Inline SVG icons — no icon library needed, no network request, no bundle bloat.
// These are simplified Heroicons (heroicons.com) paths.
const Icons = {
  jobs: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="7" width="20" height="14" rx="2" />
      <path d="M16 7V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v2" />
    </svg>
  ),
  applications: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <polyline points="14 2 14 8 20 8" />
      <line x1="9" y1="13" x2="15" y2="13" />
      <line x1="9" y1="17" x2="13" y2="17" />
    </svg>
  ),
  orchestrator: (
    // Simple "circuit / AI" icon — two nodes connected through a center
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="3" />
      <path d="M12 2v3M12 19v3M2 12h3M19 12h3" />
      <path d="M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1" />
    </svg>
  ),
  settings: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  ),
};

// NavItem: a single sidebar navigation link.
// Active state uses a left accent bar instead of a filled background — cleaner.
function NavItem({ active, onClick, icon, label }) {
  return (
    <button
      onClick={onClick}
      className={`
        w-full flex items-center gap-3 px-3 py-2 rounded-md text-left text-sm
        transition-colors duration-100
        ${active
          ? "bg-white/[0.06] text-white"
          : "text-zinc-500 hover:bg-white/[0.04] hover:text-zinc-300"
        }
      `}
    >
      <span className={active ? "text-white" : "text-zinc-500"}>{icon}</span>
      <span className="font-medium">{label}</span>
    </button>
  );
}

// Sidebar: the full left navigation panel.
// `activePage`: string — which page is currently active (controls highlight)
// `onNavigate`: function — called with the page name when user clicks a nav item
export function Sidebar({ activePage, onNavigate }) {
  // Navigation items: each has an id, an SVG icon, and a label.
  // In Phase 2+, we'll replace this manual routing with React Router.
  const navItems = [
    { id: "jobs", icon: Icons.jobs, label: "Jobs" },
    { id: "applications", icon: Icons.applications, label: "Applications" },
    { id: "orchestrator", icon: Icons.orchestrator, label: "Orchestrator" },
    { id: "settings", icon: Icons.settings, label: "Settings" },
  ];

  return (
    <aside className="w-56 min-h-screen bg-[#111111] flex flex-col border-r border-white/[0.06]">

      {/* App name */}
      <div className="px-5 py-5">
        <span className="text-white text-sm font-semibold tracking-tight">JobHunter</span>
        <span className="text-zinc-600 text-sm font-semibold tracking-tight"> AI</span>
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-3 space-y-0.5">
        {navItems.map((item) => (
          <NavItem
            key={item.id}
            active={activePage === item.id}
            onClick={() => onNavigate(item.id)}
            icon={item.icon}
            label={item.label}
          />
        ))}
      </nav>

      {/* System status footer */}
      <div className="px-5 py-4 border-t border-white/[0.06]">
        <div className="flex items-center gap-2">
          <div className="w-1.5 h-1.5 rounded-full bg-emerald-500"></div>
          <span className="text-zinc-600 text-xs">System ready</span>
        </div>
      </div>
    </aside>
  );
}
