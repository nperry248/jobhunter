/**
 * Sidebar.jsx — The left navigation sidebar.
 *
 * CONCEPT — React components:
 *   A React component is a JavaScript function that returns JSX (HTML-like syntax).
 *   Components are reusable: you can use <Sidebar /> anywhere in your app and it
 *   renders the same UI. Props (properties) let you pass data into components.
 *
 * DESIGN NOTES (Terminal Command Center aesthetic):
 *   - Brand mark: "JH" monogram in a violet square + stacked text
 *   - Nav items: thin violet left-border rule on active state (instead of filled bg)
 *     This is borrowed from terminal UI conventions — a cursor line, not a highlight.
 *   - Labels are uppercase Syne — reads as "control panel" rather than generic SaaS.
 *   - Footer: "SYSTEM" category header + animated status indicator
 */

// Inline SVG icons — no icon library needed, no network request, no bundle bloat.
const Icons = {
  jobs: (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="7" width="20" height="14" rx="2" />
      <path d="M16 7V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v2" />
    </svg>
  ),
  applications: (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <polyline points="14 2 14 8 20 8" />
      <line x1="9" y1="13" x2="15" y2="13" />
      <line x1="9" y1="17" x2="13" y2="17" />
    </svg>
  ),
  orchestrator: (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="3" />
      <path d="M12 2v3M12 19v3M2 12h3M19 12h3" />
      <path d="M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1" />
    </svg>
  ),
  settings: (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  ),
};

// ── Section label ──────────────────────────────────────────────────────────────
// Small uppercase category headers inside the sidebar — visually organizes
// the nav into sections (like a terminal's section dividers).
function SectionLabel({ children }) {
  return (
    <p className="px-4 pt-5 pb-1.5 text-[10px] font-mono font-medium tracking-[0.15em] uppercase text-[#2d3748]">
      {children}
    </p>
  );
}

// ── Nav item ───────────────────────────────────────────────────────────────────
// The key design decision here: active state uses a 2px violet left border
// instead of a filled background. This reads as a "cursor" or "selection line"
// — a terminal UI convention that feels precise and intentional.
function NavItem({ active, onClick, icon, label }) {
  return (
    <button
      onClick={onClick}
      className={`
        w-full flex items-center gap-3 px-4 py-2.5 text-left
        transition-all duration-150 border-l-2
        ${active
          ? "border-[#a78bfa] bg-[rgba(167,139,250,0.07)] text-white"
          : "border-transparent text-[#64748b] hover:bg-white/[0.03] hover:text-[#94a3b8]"
        }
      `}
    >
      {/* Icon inherits color from parent button */}
      <span className={`shrink-0 transition-colors ${active ? "text-[#a78bfa]" : ""}`}>
        {icon}
      </span>
      <span className="text-xs font-semibold tracking-widest uppercase">{label}</span>
    </button>
  );
}

// ── Sidebar ────────────────────────────────────────────────────────────────────
// `activePage`: string — which page is currently active (controls highlight)
// `onNavigate`: function — called with the page name when user clicks a nav item
export function Sidebar({ activePage, onNavigate }) {
  const navItems = [
    { id: "jobs",         icon: Icons.jobs,         label: "Jobs"         },
    { id: "applications", icon: Icons.applications, label: "Applications" },
    { id: "orchestrator", icon: Icons.orchestrator,  label: "Orchestrator" },
    { id: "settings",     icon: Icons.settings,      label: "Settings"     },
  ];

  return (
    <aside
      className="w-52 min-h-screen flex flex-col border-r"
      style={{
        backgroundColor: 'var(--bg-sidebar)',
        borderColor: 'var(--border)',
      }}
    >
      {/* ── Brand mark ── */}
      {/* "JH" monogram in a violet square + stacked wordmark.
          The violet square creates a distinctive, memorable logo mark without
          any external asset — just text in a colored box. */}
      <div className="px-4 pt-6 pb-5">
        <div className="flex items-center gap-3">
          {/* Monogram badge */}
          <div
            className="w-8 h-8 rounded-md flex items-center justify-center shrink-0"
            style={{ backgroundColor: '#a78bfa' }}
          >
            <span className="text-white text-xs font-black tracking-tight">JH</span>
          </div>
          {/* Wordmark */}
          <div>
            <p className="text-white text-sm font-bold tracking-tight leading-none">
              JobHunter
            </p>
            <p className="font-mono text-[10px] tracking-widest uppercase leading-none mt-1"
               style={{ color: 'var(--text-2)' }}>
              AI Agent
            </p>
          </div>
        </div>
      </div>

      {/* Divider */}
      <div className="mx-4 mb-1" style={{ height: '1px', backgroundColor: 'var(--border)' }} />

      {/* ── Navigation ── */}
      <nav className="flex-1">
        <SectionLabel>Navigate</SectionLabel>
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

      {/* ── System status footer ── */}
      {/* The ring-pulse animation (defined in tailwind.config.js) creates an
          expanding ring effect — more visually interesting than a simple blink. */}
      <div className="px-4 py-4" style={{ borderTop: '1px solid var(--border)' }}>
        <SectionLabel>System</SectionLabel>
        <div className="flex items-center gap-2.5 px-4 pb-1">
          {/* Relative container holds both the dot and the expanding ring */}
          <div className="relative w-2 h-2 shrink-0">
            <div className="absolute inset-0 rounded-full bg-emerald-500 animate-ring-pulse opacity-60" />
            <div className="absolute inset-0 rounded-full bg-emerald-500" />
          </div>
          <span className="font-mono text-[10px] tracking-widest uppercase"
                style={{ color: 'var(--text-2)' }}>
            Online
          </span>
        </div>
      </div>
    </aside>
  );
}
