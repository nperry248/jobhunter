/**
 * ApplicationsPage.jsx — Placeholder for the applications tracking view.
 * Will display submitted applications with status, screenshots, and timestamps.
 * Implemented in Session 3 after the Apply Agent is built.
 */
export function ApplicationsPage() {
  return (
    <div className="p-8">
      <div className="mb-8">
        <h2 className="text-lg font-semibold text-white">Applications</h2>
        <p className="text-zinc-500 text-sm mt-1">Submitted applications and their current statuses.</p>
      </div>

      <div className="border border-white/[0.06] rounded-lg p-16 text-center">
        <div className="w-8 h-8 mx-auto mb-4 text-zinc-700">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
            <polyline points="14 2 14 8 20 8" />
            <line x1="9" y1="13" x2="15" y2="13" />
            <line x1="9" y1="17" x2="13" y2="17" />
          </svg>
        </div>
        <p className="text-zinc-500 text-sm">No applications yet.</p>
        <p className="text-zinc-700 text-xs mt-1">The Apply Agent submits applications automatically.</p>
      </div>
    </div>
  );
}
