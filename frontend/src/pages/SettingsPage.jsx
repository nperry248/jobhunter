/**
 * SettingsPage.jsx — Placeholder for user profile and job preferences.
 * Will let you configure: target job titles, locations, company blocklist,
 * match score threshold, and upload your resume.
 * Implemented in Session 2 alongside the Resume Match Agent.
 */
export function SettingsPage() {
  return (
    <div className="p-8">
      <div className="mb-8">
        <h2 className="text-lg font-semibold text-white">Settings</h2>
        <p className="text-zinc-500 text-sm mt-1">Configure your profile, resume, and agent behavior.</p>
      </div>

      <div className="border border-white/[0.06] rounded-lg p-16 text-center">
        <div className="w-8 h-8 mx-auto mb-4 text-zinc-700">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
          </svg>
        </div>
        <p className="text-zinc-500 text-sm">Settings UI coming in Session 2.</p>
        <p className="text-zinc-700 text-xs mt-1">
          For now, use the API directly at{" "}
          <code className="text-zinc-500 font-mono">localhost:8000/docs</code>
        </p>
      </div>
    </div>
  );
}
