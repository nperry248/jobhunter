/**
 * JobsPage.jsx — The main jobs dashboard.
 *
 * WHAT THIS PAGE DOES:
 *   1. Fetches jobs from GET /api/v1/jobs on mount and when filters change
 *   2. Shows a status filter bar (All / New / Scored / Reviewed / Ignored)
 *   3. Renders each job as a card with a color-coded match score badge
 *   4. Lets the user mark jobs as "reviewed" or "ignored" via PATCH /api/v1/jobs/{id}
 *   5. Paginates results (20 per page)
 *
 * REACT CONCEPTS INTRODUCED HERE:
 *   - useEffect: run code after render (used here for data fetching)
 *   - Multiple useState hooks: each piece of UI state gets its own hook
 *   - Conditional rendering: show loading/error/empty/data states
 *   - useCallback: memoize a function so it's stable across renders
 */

import { useState, useEffect, useCallback, useRef } from "react";
import { getJobs, updateJobStatus, clearAllJobs, runPipeline, getPipelineStatus } from "../api/client";

// How many jobs to show per page. Matches the API default.
const PAGE_SIZE = 20;

// The filter tabs shown at the top of the page.
// `value: null` means "no filter" — return all statuses.
const STATUS_FILTERS = [
  { label: "All",      value: null },
  { label: "New",      value: "new" },
  { label: "Scored",   value: "scored" },
  { label: "Reviewed", value: "reviewed" },
  { label: "Ignored",  value: "ignored" },
  { label: "Applied",  value: "applied" },
  { label: "Failed",   value: "failed" },
];


// ── Score Badge ───────────────────────────────────────────────────────────────
// Returns a label + Tailwind class for a score value.
// Color encodes the score range so you can scan the list at a glance.

function scoreBadge(score) {
  if (score === null || score === undefined) {
    return { label: "—", className: "bg-zinc-800 text-zinc-500" };
  }
  if (score >= 90) return { label: Math.round(score), className: "bg-emerald-500/15 text-emerald-400" };
  if (score >= 70) return { label: Math.round(score), className: "bg-blue-500/15 text-blue-400" };
  if (score >= 50) return { label: Math.round(score), className: "bg-yellow-500/15 text-yellow-400" };
  if (score >= 30) return { label: Math.round(score), className: "bg-orange-500/15 text-orange-400" };
  return           { label: Math.round(score), className: "bg-red-500/15 text-red-400" };
}


// ── Status Pill ───────────────────────────────────────────────────────────────

const STATUS_STYLES = {
  new:      "bg-zinc-700/50 text-zinc-400",
  scored:   "bg-blue-500/10 text-blue-400",
  reviewed: "bg-emerald-500/10 text-emerald-400",
  ignored:  "bg-zinc-800 text-zinc-600",
  applied:  "bg-purple-500/10 text-purple-400",
  failed:   "bg-red-500/10 text-red-400",
};

function StatusPill({ status }) {
  return (
    <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${STATUS_STYLES[status] ?? "bg-zinc-800 text-zinc-500"}`}>
      {status}
    </span>
  );
}


// ── Job Card ──────────────────────────────────────────────────────────────────
// Clicking anywhere on the main row (title / company / reasoning) toggles an
// expanded detail panel below the card. The chevron button also toggles it.
// Action buttons (View ↗, Reviewed, Ignore, Undo) stop propagation so they
// don't accidentally toggle the expand state when clicked.

function JobCard({ job, onStatusChange }) {
  const badge = scoreBadge(job.match_score);
  const [expanded, setExpanded] = useState(false);

  return (
    <div className={`border border-white/[0.06] rounded-lg transition-opacity duration-200 ${job.status === "ignored" ? "opacity-40" : ""}`}>

      {/* ── Main row — always visible ── */}
      <div className="p-4 flex items-start gap-4">

        {/* Score badge */}
        <div className={`flex-shrink-0 w-12 h-12 rounded-lg flex items-center justify-center text-sm font-semibold ${badge.className}`}>
          {badge.label}
        </div>

        {/* Clickable content area — toggles expansion */}
        <button
          onClick={() => setExpanded(e => !e)}
          className="flex-1 min-w-0 text-left"
        >
          <div className="flex items-center gap-2 flex-wrap">
            <h3 className="text-sm font-semibold text-white">{job.title}</h3>
            <StatusPill status={job.status} />
          </div>

          <p className="text-zinc-400 text-sm mt-0.5">
            {job.company}
            {job.location && <span className="text-zinc-600"> · {job.location}</span>}
          </p>

          {/* Reasoning preview — clamped to 2 lines when collapsed */}
          {job.match_reasoning && (
            <p className="text-zinc-500 text-xs mt-1.5 line-clamp-2">{job.match_reasoning}</p>
          )}
        </button>

        {/* Actions — stopPropagation so clicks don't toggle expand */}
        <div className="flex-shrink-0 flex items-center gap-2" onClick={e => e.stopPropagation()}>

          {/* Expand / collapse chevron */}
          <button
            onClick={() => setExpanded(e => !e)}
            className="text-zinc-600 hover:text-zinc-400 transition-colors p-1"
            title={expanded ? "Collapse" : "Expand"}
          >
            {/* Chevron rotates 180° when expanded */}
            <svg
              className={`w-4 h-4 transition-transform duration-200 ${expanded ? "rotate-180" : ""}`}
              viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
              strokeLinecap="round" strokeLinejoin="round"
            >
              <path d="M6 9l6 6 6-6" />
            </svg>
          </button>

          <a
            href={job.source_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-zinc-500 hover:text-zinc-300 transition-colors px-2 py-1 rounded border border-white/[0.06] hover:border-white/[0.12]"
          >
            View ↗
          </a>

          {/* Scored: show Reviewed + Ignore buttons */}
          {job.status === "scored" && (
            <>
              <button
                onClick={() => onStatusChange(job.id, "reviewed")}
                className="text-xs text-zinc-400 hover:text-emerald-400 transition-colors px-2 py-1 rounded border border-white/[0.06] hover:border-emerald-500/30"
              >
                Reviewed
              </button>
              <button
                onClick={() => onStatusChange(job.id, "ignored")}
                className="text-xs text-zinc-600 hover:text-zinc-400 transition-colors px-2 py-1 rounded border border-white/[0.06]"
              >
                Ignore
              </button>
            </>
          )}

          {/* Reviewed: show Undo button to go back to scored */}
          {job.status === "reviewed" && (
            <button
              onClick={() => onStatusChange(job.id, "scored")}
              className="text-xs text-zinc-600 hover:text-zinc-400 transition-colors px-2 py-1 rounded border border-white/[0.06]"
            >
              Undo
            </button>
          )}
        </div>
      </div>

      {/* ── Expanded detail panel ── */}
      {expanded && (
        <div className="border-t border-white/[0.06] px-4 py-3 space-y-3">

          {/* Full reasoning text (no clamp) */}
          {job.match_reasoning && (
            <div>
              <p className="text-xs text-zinc-600 font-medium mb-1 uppercase tracking-wide">Match Reasoning</p>
              <p className="text-zinc-400 text-xs leading-relaxed">{job.match_reasoning}</p>
            </div>
          )}

          {/* Metadata row: source, dates */}
          <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs">
            {job.source && (
              <span className="text-zinc-600">
                Source: <span className="text-zinc-400 capitalize">{job.source}</span>
              </span>
            )}
            {job.scraped_at && (
              <span className="text-zinc-600">
                Scraped: <span className="text-zinc-400">{new Date(job.scraped_at).toLocaleDateString()}</span>
              </span>
            )}
            {job.scored_at && (
              <span className="text-zinc-600">
                Scored: <span className="text-zinc-400">{new Date(job.scored_at).toLocaleDateString()}</span>
              </span>
            )}
            {job.applied_at && (
              <span className="text-zinc-600">
                Applied: <span className="text-zinc-400">{new Date(job.applied_at).toLocaleDateString()}</span>
              </span>
            )}
          </div>

          {/* Full URL */}
          {job.source_url && (
            <p className="text-xs text-zinc-600 break-all">
              <span className="mr-1">URL:</span>
              <a
                href={job.source_url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-blue-500/70 hover:text-blue-400 transition-colors"
              >
                {job.source_url}
              </a>
            </p>
          )}
        </div>
      )}
    </div>
  );
}


// ── Loading Skeleton ──────────────────────────────────────────────────────────
// Shown while the API request is in flight. Prevents layout shift.

function SkeletonCard() {
  return (
    <div className="border border-white/[0.06] rounded-lg p-4 animate-pulse">
      <div className="flex items-start gap-4">
        <div className="w-12 h-12 rounded-lg bg-white/[0.04] flex-shrink-0" />
        <div className="flex-1 space-y-2 pt-1">
          <div className="h-4 bg-white/[0.04] rounded w-1/3" />
          <div className="h-3 bg-white/[0.04] rounded w-1/4" />
          <div className="h-3 bg-white/[0.04] rounded w-2/3" />
        </div>
      </div>
    </div>
  );
}


// ── Main Page Component ───────────────────────────────────────────────────────

export function JobsPage() {
  // ── State ──────────────────────────────────────────────────────────────────
  const [jobs, setJobs]       = useState([]);
  const [total, setTotal]     = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState(null);
  const [filter, setFilter]   = useState(null);   // active status filter
  const [offset, setOffset]   = useState(0);      // pagination offset
  const [clearing, setClearing] = useState(false); // clear-all in flight
  const [confirmClear, setConfirmClear] = useState(false); // show confirmation
  const [pipelineRunning, setPipelineRunning] = useState(false); // pipeline in progress
  const [pipelineResult, setPipelineResult] = useState(null);   // last run summary
  const pollRef = useRef(null); // holds the setInterval ID so we can clear it

  // ── Data Fetching ──────────────────────────────────────────────────────────
  // CONCEPT — useEffect:
  //   After React renders the component, useEffect runs the function inside.
  //   The dependency array [filter, offset] means "re-run when these change."
  //   Without it, the fetch would run on every single render — a bug.
  //
  // CONCEPT — useCallback:
  //   Wrapping fetchJobs in useCallback means React reuses the same function
  //   reference between renders (unless filter/offset changed). Without this,
  //   `fetchJobs` would be a new function object on every render, and the
  //   useEffect dependency array would trigger an infinite loop.

  const fetchJobs = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getJobs({ status: filter, limit: PAGE_SIZE, offset });
      setJobs(data.jobs);
      setTotal(data.total);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [filter, offset]);

  useEffect(() => {
    fetchJobs();
  }, [fetchJobs]);

  // ── Status Change (Optimistic Update) ─────────────────────────────────────
  // CONCEPT — Optimistic update:
  //   Update local state immediately so the UI responds instantly, then
  //   persist to the API in the background. Feels much snappier than waiting
  //   for the server response before updating the UI.

  async function handleStatusChange(jobId, newStatus) {
    setJobs(prev => prev.map(j => j.id === jobId ? { ...j, status: newStatus } : j));
    try {
      await updateJobStatus(jobId, newStatus);
    } catch (err) {
      console.error("Failed to update job status:", err);
    }
  }

  // ── Clear All Jobs ─────────────────────────────────────────────────────────
  async function handleClearAll() {
    setClearing(true);
    try {
      await clearAllJobs();
      setJobs([]);
      setTotal(0);
      setOffset(0);
    } catch (err) {
      console.error("Failed to clear jobs:", err);
    } finally {
      setClearing(false);
      setConfirmClear(false);
    }
  }

  // ── Run Pipeline ───────────────────────────────────────────────────────────
  // CONCEPT — Polling:
  //   The pipeline runs in the background on the server. We don't get a
  //   WebSocket or push notification when it finishes. Instead we poll
  //   GET /pipeline/status every 3 seconds until `running` goes false.
  //   When it does, we stop polling and refresh the jobs list.
  //
  //   useRef stores the interval ID so we can clear it from anywhere — if we
  //   stored it in useState, clearing it would trigger a re-render.

  async function handleRunPipeline() {
    try {
      await runPipeline();
      setPipelineRunning(true);

      // Start polling every 3 seconds
      pollRef.current = setInterval(async () => {
        try {
          const status = await getPipelineStatus();
          if (!status.running) {
            // Pipeline finished — stop polling, store result, refresh jobs
            clearInterval(pollRef.current);
            pollRef.current = null;
            setPipelineRunning(false);
            setPipelineResult(status.last_error
              ? { error: status.last_error }
              : status.last_result
            );
            fetchJobs();
          }
        } catch {
          // Polling error — keep trying, don't stop
        }
      }, 3000);
    } catch (err) {
      console.error("Failed to start pipeline:", err);
    }
  }

  // Clean up the polling interval if the component unmounts mid-run
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  // ── Filter Change ──────────────────────────────────────────────────────────
  function handleFilterChange(value) {
    setFilter(value);
    setOffset(0); // reset to page 1 whenever the filter changes
  }

  // ── Pagination ─────────────────────────────────────────────────────────────
  const totalPages  = Math.ceil(total / PAGE_SIZE);
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1;

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div className="p-8 max-w-4xl">

      {/* Header */}
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-white">Jobs</h2>
          <p className="text-zinc-500 text-sm mt-0.5">
            {loading ? "Loading…" : `${total} job${total !== 1 ? "s" : ""} found`}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {/* Run Now — triggers scrape + score pipeline, polls until done */}
          <button
            onClick={handleRunPipeline}
            disabled={pipelineRunning}
            className={`text-xs transition-colors px-3 py-1.5 rounded border ${
              pipelineRunning
                ? "text-blue-400 border-blue-500/30 cursor-not-allowed"
                : "text-zinc-400 hover:text-blue-400 border-white/[0.06] hover:border-blue-500/30"
            }`}
          >
            {pipelineRunning ? (
              <span className="flex items-center gap-1.5">
                <span className="inline-block w-2 h-2 rounded-full bg-blue-400 animate-pulse" />
                Running…
              </span>
            ) : "Run Now"}
          </button>
          <button
            onClick={fetchJobs}
            className="text-xs text-zinc-500 hover:text-zinc-300 transition-colors px-3 py-1.5 rounded border border-white/[0.06] hover:border-white/[0.12]"
          >
            Refresh
          </button>
          {!confirmClear ? (
            <button
              onClick={() => setConfirmClear(true)}
              className="text-xs text-zinc-600 hover:text-red-400 transition-colors px-3 py-1.5 rounded border border-white/[0.06] hover:border-red-500/30"
            >
              Clear All
            </button>
          ) : (
            <div className="flex items-center gap-2 px-3 py-1.5 rounded border border-red-500/30 bg-red-500/5">
              <span className="text-xs text-red-400">Delete all jobs?</span>
              <button
                onClick={handleClearAll}
                disabled={clearing}
                className="text-xs text-red-400 hover:text-red-300 font-medium disabled:opacity-50 transition-colors"
              >
                {clearing ? "Clearing…" : "Yes"}
              </button>
              <button
                onClick={() => setConfirmClear(false)}
                className="text-xs text-zinc-500 hover:text-zinc-300 transition-colors"
              >
                Cancel
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Pipeline Result Banner */}
      {pipelineResult && !pipelineRunning && (
        <div className={`mb-4 px-4 py-3 rounded-lg border text-xs flex items-center justify-between ${
          pipelineResult.error
            ? "border-red-500/20 bg-red-500/5 text-red-400"
            : "border-white/[0.06] bg-white/[0.02] text-zinc-400"
        }`}>
          {pipelineResult.error ? (
            <span>Pipeline error: {pipelineResult.error}</span>
          ) : (
            <span>
              Last run: <span className="text-white">{pipelineResult.scrape?.total_new ?? 0} new jobs</span> scraped,{" "}
              <span className="text-white">{pipelineResult.score?.total_scored ?? 0} scored</span>
              {pipelineResult.scrape?.total_duplicate > 0 && (
                <span className="text-zinc-600"> · {pipelineResult.scrape.total_duplicate} duplicates skipped</span>
              )}
            </span>
          )}
          <button onClick={() => setPipelineResult(null)} className="text-zinc-600 hover:text-zinc-400 ml-4">✕</button>
        </div>
      )}

      {/* Filter Tabs */}
      <div className="flex gap-1 mb-6 bg-white/[0.03] p-1 rounded-lg w-fit">
        {STATUS_FILTERS.map(({ label, value }) => (
          <button
            key={label}
            onClick={() => handleFilterChange(value)}
            className={`px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
              filter === value ? "bg-white/[0.08] text-white" : "text-zinc-500 hover:text-zinc-300"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Error State */}
      {error && (
        <div className="border border-red-500/20 bg-red-500/5 rounded-lg p-4 mb-6">
          <p className="text-red-400 text-sm font-medium">Failed to load jobs</p>
          <p className="text-red-500/70 text-xs mt-1">{error}</p>
          <p className="text-zinc-600 text-xs mt-2">
            Is the backend running?{" "}
            <code className="text-zinc-500">uvicorn api.main:app --reload --port 8000</code>
          </p>
        </div>
      )}

      {/* Job List */}
      <div className="space-y-2">
        {loading ? (
          Array.from({ length: 5 }).map((_, i) => <SkeletonCard key={i} />)
        ) : jobs.length === 0 ? (
          <div className="border border-white/[0.06] rounded-lg p-16 text-center">
            <div className="w-8 h-8 mx-auto mb-4 text-zinc-700">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <rect x="2" y="7" width="20" height="14" rx="2" />
                <path d="M16 7V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v2" />
              </svg>
            </div>
            <p className="text-zinc-500 text-sm">No jobs found.</p>
            <p className="text-zinc-700 text-xs mt-1">
              {filter
                ? `No jobs with status "${filter}". Try a different filter.`
                : "Run the Scraper Agent to populate this list."}
            </p>
          </div>
        ) : (
          jobs.map(job => (
            <JobCard key={job.id} job={job} onStatusChange={handleStatusChange} />
          ))
        )}
      </div>

      {/* Pagination */}
      {!loading && totalPages > 1 && (
        <div className="flex items-center justify-between mt-6 pt-4 border-t border-white/[0.06]">
          <button
            onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            disabled={offset === 0}
            className="text-sm text-zinc-500 hover:text-zinc-300 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            ← Prev
          </button>
          <span className="text-xs text-zinc-600">Page {currentPage} of {totalPages}</span>
          <button
            onClick={() => setOffset(offset + PAGE_SIZE)}
            disabled={offset + PAGE_SIZE >= total}
            className="text-sm text-zinc-500 hover:text-zinc-300 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            Next →
          </button>
        </div>
      )}

    </div>
  );
}
