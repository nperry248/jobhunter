/**
 * JobsPage.jsx — Placeholder for the jobs list view.
 * Will display scraped jobs with status, match score, and action buttons.
 * Implemented in Session 2 after the Scraper Agent is built.
 */
export function JobsPage() {
  return (
    <div className="p-8">
      <div className="mb-8">
        <h2 className="text-lg font-semibold text-white">Jobs</h2>
        <p className="text-zinc-500 text-sm mt-1">Scraped listings from LinkedIn, Indeed, and more.</p>
      </div>

      {/* Empty state — replaced with job cards in Session 2 */}
      <div className="border border-white/[0.06] rounded-lg p-16 text-center">
        <div className="w-8 h-8 mx-auto mb-4 text-zinc-700">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <rect x="2" y="7" width="20" height="14" rx="2" />
            <path d="M16 7V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v2" />
          </svg>
        </div>
        <p className="text-zinc-500 text-sm">No jobs yet.</p>
        <p className="text-zinc-700 text-xs mt-1">Run the Scraper Agent to populate this list.</p>
      </div>
    </div>
  );
}
