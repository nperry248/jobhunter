/**
 * ApplicationsPage.jsx — Placeholder for the applications tracking view.
 * Will display submitted applications with status, screenshots, and timestamps.
 * Implemented in Session 3 after the Apply Agent is built.
 */
export function ApplicationsPage() {
  return (
    <div className="p-8 max-w-4xl">

      {/* Header */}
      <div className="mb-8">
        <p className="font-mono text-[10px] tracking-[0.2em] uppercase mb-1" style={{ color: '#334155' }}>
          Submission Log
        </p>
        <h1 className="text-2xl font-bold tracking-tight" style={{ color: 'var(--text-1)' }}>
          Applications
        </h1>
        <p className="text-sm mt-1" style={{ color: 'var(--text-2)' }}>
          Submitted applications and their current statuses.
        </p>
      </div>

      {/* Empty state */}
      <div
        className="rounded-lg p-16 text-center"
        style={{ border: '1px solid var(--border)', backgroundColor: 'var(--bg-elevated)' }}
      >
        <div className="w-8 h-8 mx-auto mb-4" style={{ color: '#1e293b' }}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
            <polyline points="14 2 14 8 20 8" />
            <line x1="9" y1="13" x2="15" y2="13" />
            <line x1="9" y1="17" x2="13" y2="17" />
          </svg>
        </div>
        <p className="text-sm font-medium mb-1" style={{ color: '#475569' }}>No applications yet</p>
        <p className="font-mono text-[10px] tracking-wider" style={{ color: '#1e293b' }}>
          THE APPLY AGENT SUBMITS APPLICATIONS AUTOMATICALLY
        </p>
      </div>

    </div>
  );
}
