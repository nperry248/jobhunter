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
// Returns a label + style for a score value.
// Color encodes the score range so you can scan the list at a glance.
// Using JetBrains Mono so scores read as precise data, not decorative text.

function scoreBadge(score) {
  if (score === null || score === undefined) {
    return { label: "—", bg: "rgba(255,255,255,0.04)", color: "#475569", border: "rgba(255,255,255,0.06)" };
  }
  const n = Math.round(score);
  if (score >= 90) return { label: n, bg: "rgba(16,185,129,0.1)",  color: "#34d399", border: "rgba(16,185,129,0.2)"  };
  if (score >= 70) return { label: n, bg: "rgba(99,179,237,0.1)",  color: "#60a5fa", border: "rgba(99,179,237,0.2)"  };
  if (score >= 50) return { label: n, bg: "rgba(251,191,36,0.1)",  color: "#fbbf24", border: "rgba(251,191,36,0.2)"  };
  if (score >= 30) return { label: n, bg: "rgba(251,146,60,0.1)",  color: "#fb923c", border: "rgba(251,146,60,0.2)"  };
  return               { label: n, bg: "rgba(248,113,113,0.1)",  color: "#f87171", border: "rgba(248,113,113,0.2)"  };
}


// ── Status Pill ───────────────────────────────────────────────────────────────
// Monospace font + compact pill — reads as a system status tag.

const STATUS_CONFIG = {
  new:      { color: "#64748b", bg: "rgba(100,116,139,0.12)" },
  scored:   { color: "#60a5fa", bg: "rgba(96,165,250,0.1)"  },
  reviewed: { color: "#34d399", bg: "rgba(52,211,153,0.1)"  },
  ignored:  { color: "#334155", bg: "rgba(51,65,85,0.3)"    },
  applied:  { color: "#a78bfa", bg: "rgba(167,139,250,0.1)" },
  failed:   { color: "#f87171", bg: "rgba(248,113,113,0.1)" },
};

function StatusPill({ status }) {
  const cfg = STATUS_CONFIG[status] ?? { color: "#64748b", bg: "rgba(100,116,139,0.1)" };
  return (
    <span
      className="font-mono text-[10px] font-medium px-2 py-0.5 rounded tracking-widest uppercase"
      style={{ color: cfg.color, backgroundColor: cfg.bg }}
    >
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
    <div
      className={`rounded-lg transition-opacity duration-200 ${job.status === "ignored" ? "opacity-35" : ""}`}
      style={{
        backgroundColor: 'var(--bg-elevated)',
        border: '1px solid var(--border)',
      }}
    >
      {/* ── Main row — always visible ── */}
      <div className="p-4 flex items-start gap-4">

        {/* Score badge — monospace font makes the number feel like a data readout */}
        <div
          className="shrink-0 w-12 h-12 rounded-lg flex items-center justify-center font-mono text-sm font-semibold"
          style={{
            backgroundColor: badge.bg,
            color: badge.color,
            border: `1px solid ${badge.border}`,
          }}
        >
          {badge.label}
        </div>

        {/* Clickable content area — toggles expansion */}
        <button
          onClick={() => setExpanded(e => !e)}
          className="flex-1 min-w-0 text-left"
        >
          <div className="flex items-center gap-2 flex-wrap">
            <h3 className="text-sm font-semibold" style={{ color: 'var(--text-1)' }}>
              {job.title}
            </h3>
            <StatusPill status={job.status} />
          </div>

          <p className="text-sm mt-0.5" style={{ color: 'var(--text-2)' }}>
            {job.company}
            {job.location && (
              <span className="font-mono text-xs ml-1.5" style={{ color: '#334155' }}>
                / {job.location}
              </span>
            )}
          </p>

          {/* Reasoning preview — clamped to 2 lines when collapsed */}
          {job.match_reasoning && (
            <p className="text-xs mt-1.5 line-clamp-2 leading-relaxed" style={{ color: '#475569' }}>
              {job.match_reasoning}
            </p>
          )}
        </button>

        {/* Actions — stopPropagation so clicks don't toggle expand */}
        <div className="shrink-0 flex items-center gap-2" onClick={e => e.stopPropagation()}>

          {/* Expand / collapse chevron */}
          <button
            onClick={() => setExpanded(e => !e)}
            className="p-1 transition-colors rounded"
            style={{ color: '#334155' }}
            onMouseEnter={e => e.currentTarget.style.color = '#64748b'}
            onMouseLeave={e => e.currentTarget.style.color = '#334155'}
            title={expanded ? "Collapse" : "Expand"}
          >
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
            className="font-mono text-[10px] tracking-wider px-2.5 py-1.5 rounded transition-all"
            style={{
              color: '#64748b',
              border: '1px solid var(--border)',
            }}
            onMouseEnter={e => { e.currentTarget.style.color = 'var(--text-1)'; e.currentTarget.style.borderColor = 'var(--border-hover)'; }}
            onMouseLeave={e => { e.currentTarget.style.color = '#64748b'; e.currentTarget.style.borderColor = 'var(--border)'; }}
          >
            VIEW ↗
          </a>

          {/* Scored: show Reviewed + Ignore buttons */}
          {job.status === "scored" && (
            <>
              <button
                onClick={() => onStatusChange(job.id, "reviewed")}
                className="font-mono text-[10px] tracking-wider px-2.5 py-1.5 rounded transition-all"
                style={{ color: '#34d399', border: '1px solid rgba(52,211,153,0.2)' }}
                onMouseEnter={e => { e.currentTarget.style.backgroundColor = 'rgba(52,211,153,0.07)'; }}
                onMouseLeave={e => { e.currentTarget.style.backgroundColor = 'transparent'; }}
              >
                APPROVE
              </button>
              <button
                onClick={() => onStatusChange(job.id, "ignored")}
                className="font-mono text-[10px] tracking-wider px-2.5 py-1.5 rounded transition-all"
                style={{ color: '#475569', border: '1px solid var(--border)' }}
                onMouseEnter={e => e.currentTarget.style.color = '#64748b'}
                onMouseLeave={e => e.currentTarget.style.color = '#475569'}
              >
                SKIP
              </button>
            </>
          )}

          {/* Reviewed: show Undo button to go back to scored */}
          {job.status === "reviewed" && (
            <button
              onClick={() => onStatusChange(job.id, "scored")}
              className="font-mono text-[10px] tracking-wider px-2.5 py-1.5 rounded transition-all"
              style={{ color: '#475569', border: '1px solid var(--border)' }}
              onMouseEnter={e => e.currentTarget.style.color = '#64748b'}
              onMouseLeave={e => e.currentTarget.style.color = '#475569'}
            >
              UNDO
            </button>
          )}
        </div>
      </div>

      {/* ── Expanded detail panel ── */}
      {expanded && (
        <div style={{ borderTop: '1px solid var(--border)' }} className="px-4 py-3 space-y-3">

          {/* Full reasoning text (no clamp) */}
          {job.match_reasoning && (
            <div>
              <p className="font-mono text-[10px] tracking-widest uppercase mb-1.5" style={{ color: '#334155' }}>
                Match Reasoning
              </p>
              <p className="text-xs leading-relaxed" style={{ color: '#64748b' }}>
                {job.match_reasoning}
              </p>
            </div>
          )}

          {/* Metadata row: source, dates */}
          <div className="flex flex-wrap gap-x-6 gap-y-1">
            {job.source && (
              <span className="font-mono text-[10px]" style={{ color: '#334155' }}>
                SOURCE: <span style={{ color: '#64748b' }}>{job.source.toUpperCase()}</span>
              </span>
            )}
            {job.scraped_at && (
              <span className="font-mono text-[10px]" style={{ color: '#334155' }}>
                SCRAPED: <span style={{ color: '#64748b' }}>{new Date(job.scraped_at).toLocaleDateString()}</span>
              </span>
            )}
            {job.scored_at && (
              <span className="font-mono text-[10px]" style={{ color: '#334155' }}>
                SCORED: <span style={{ color: '#64748b' }}>{new Date(job.scored_at).toLocaleDateString()}</span>
              </span>
            )}
            {job.applied_at && (
              <span className="font-mono text-[10px]" style={{ color: '#334155' }}>
                APPLIED: <span style={{ color: '#34d399' }}>{new Date(job.applied_at).toLocaleDateString()}</span>
              </span>
            )}
          </div>

          {/* Full URL */}
          {job.source_url && (
            <p className="font-mono text-[10px] break-all" style={{ color: '#334155' }}>
              URL:{" "}
              <a
                href={job.source_url}
                target="_blank"
                rel="noopener noreferrer"
                className="transition-colors"
                style={{ color: '#a78bfa' }}
                onMouseEnter={e => e.currentTarget.style.color = '#c4b5fd'}
                onMouseLeave={e => e.currentTarget.style.color = '#a78bfa'}
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
    <div
      className="rounded-lg p-4 animate-pulse"
      style={{ backgroundColor: 'var(--bg-elevated)', border: '1px solid var(--border)' }}
    >
      <div className="flex items-start gap-4">
        <div className="w-12 h-12 rounded-lg shrink-0" style={{ backgroundColor: 'rgba(255,255,255,0.04)' }} />
        <div className="flex-1 space-y-2 pt-1">
          <div className="h-4 rounded w-1/3" style={{ backgroundColor: 'rgba(255,255,255,0.04)' }} />
          <div className="h-3 rounded w-1/4" style={{ backgroundColor: 'rgba(255,255,255,0.04)' }} />
          <div className="h-3 rounded w-2/3" style={{ backgroundColor: 'rgba(255,255,255,0.04)' }} />
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

  async function handleRunPipeline() {
    try {
      await runPipeline();
      setPipelineRunning(true);

      pollRef.current = setInterval(async () => {
        try {
          const status = await getPipelineStatus();
          if (!status.running) {
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

      {/* ── Header ── */}
      <div className="mb-8 flex items-start justify-between">
        <div>
          {/* Section label in monospace — "terminal header" feel */}
          <p className="font-mono text-[10px] tracking-[0.2em] uppercase mb-1" style={{ color: '#334155' }}>
            Job Pipeline
          </p>
          <h1 className="text-2xl font-bold tracking-tight" style={{ color: 'var(--text-1)' }}>
            Jobs
          </h1>
          <p className="text-sm mt-1" style={{ color: 'var(--text-2)' }}>
            {loading ? "Loading…" : `${total} listing${total !== 1 ? "s" : ""} found`}
          </p>
        </div>

        {/* Action buttons — monospace, bordered, small */}
        <div className="flex items-center gap-2 mt-1">
          <button
            onClick={handleRunPipeline}
            disabled={pipelineRunning}
            className="font-mono text-[10px] tracking-wider px-3 py-2 rounded transition-all flex items-center gap-2"
            style={{
              border: pipelineRunning ? '1px solid rgba(96,165,250,0.3)' : '1px solid var(--border)',
              color: pipelineRunning ? '#60a5fa' : '#64748b',
              cursor: pipelineRunning ? 'not-allowed' : 'pointer',
            }}
          >
            {pipelineRunning ? (
              <>
                <span className="inline-block w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse" />
                RUNNING
              </>
            ) : "RUN NOW"}
          </button>

          <button
            onClick={fetchJobs}
            className="font-mono text-[10px] tracking-wider px-3 py-2 rounded transition-all"
            style={{ border: '1px solid var(--border)', color: '#64748b' }}
            onMouseEnter={e => e.currentTarget.style.color = 'var(--text-1)'}
            onMouseLeave={e => e.currentTarget.style.color = '#64748b'}
          >
            REFRESH
          </button>

          {!confirmClear ? (
            <button
              onClick={() => setConfirmClear(true)}
              className="font-mono text-[10px] tracking-wider px-3 py-2 rounded transition-all"
              style={{ border: '1px solid var(--border)', color: '#334155' }}
              onMouseEnter={e => { e.currentTarget.style.color = '#f87171'; e.currentTarget.style.borderColor = 'rgba(248,113,113,0.3)'; }}
              onMouseLeave={e => { e.currentTarget.style.color = '#334155'; e.currentTarget.style.borderColor = 'var(--border)'; }}
            >
              CLEAR ALL
            </button>
          ) : (
            <div
              className="flex items-center gap-2 px-3 py-2 rounded"
              style={{ border: '1px solid rgba(248,113,113,0.3)', backgroundColor: 'rgba(248,113,113,0.05)' }}
            >
              <span className="font-mono text-[10px] tracking-wider" style={{ color: '#f87171' }}>DELETE ALL?</span>
              <button
                onClick={handleClearAll}
                disabled={clearing}
                className="font-mono text-[10px] font-semibold tracking-wider transition-colors"
                style={{ color: '#f87171' }}
              >
                {clearing ? "…" : "YES"}
              </button>
              <button
                onClick={() => setConfirmClear(false)}
                className="font-mono text-[10px] tracking-wider transition-colors"
                style={{ color: '#64748b' }}
              >
                NO
              </button>
            </div>
          )}
        </div>
      </div>

      {/* ── Pipeline Result Banner ── */}
      {pipelineResult && !pipelineRunning && (
        <div
          className="mb-5 px-4 py-3 rounded-lg flex items-center justify-between"
          style={{
            border: pipelineResult.error ? '1px solid rgba(248,113,113,0.2)' : '1px solid var(--border)',
            backgroundColor: pipelineResult.error ? 'rgba(248,113,113,0.05)' : 'var(--bg-elevated)',
          }}
        >
          {pipelineResult.error ? (
            <span className="font-mono text-xs" style={{ color: '#f87171' }}>
              ERROR: {pipelineResult.error}
            </span>
          ) : (
            <span className="font-mono text-xs" style={{ color: '#64748b' }}>
              LAST RUN:{" "}
              <span style={{ color: 'var(--text-1)' }}>{pipelineResult.scrape?.total_new ?? 0} new</span>
              {" "}scraped,{" "}
              <span style={{ color: 'var(--text-1)' }}>{pipelineResult.score?.total_scored ?? 0} scored</span>
              {pipelineResult.scrape?.total_duplicate > 0 && (
                <span style={{ color: '#334155' }}> · {pipelineResult.scrape.total_duplicate} duplicates skipped</span>
              )}
            </span>
          )}
          <button
            onClick={() => setPipelineResult(null)}
            className="font-mono text-xs ml-4 transition-colors"
            style={{ color: '#334155' }}
            onMouseEnter={e => e.currentTarget.style.color = '#64748b'}
            onMouseLeave={e => e.currentTarget.style.color = '#334155'}
          >
            ✕
          </button>
        </div>
      )}

      {/* ── Filter Tabs ── */}
      {/* Horizontal scroll wrapper so tabs never wrap on small screens */}
      <div className="mb-6 overflow-x-auto">
        <div
          className="flex gap-1 p-1 rounded-lg w-fit"
          style={{ backgroundColor: 'rgba(255,255,255,0.03)' }}
        >
          {STATUS_FILTERS.map(({ label, value }) => (
            <button
              key={label}
              onClick={() => handleFilterChange(value)}
              className="font-mono text-[10px] font-medium tracking-widest uppercase px-3.5 py-2 rounded-md transition-all whitespace-nowrap"
              style={filter === value
                ? { backgroundColor: 'rgba(255,255,255,0.08)', color: 'var(--text-1)' }
                : { color: '#475569' }
              }
              onMouseEnter={e => { if (filter !== value) e.currentTarget.style.color = '#94a3b8'; }}
              onMouseLeave={e => { if (filter !== value) e.currentTarget.style.color = '#475569'; }}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* ── Error State ── */}
      {error && (
        <div
          className="rounded-lg p-4 mb-6"
          style={{ border: '1px solid rgba(248,113,113,0.2)', backgroundColor: 'rgba(248,113,113,0.05)' }}
        >
          <p className="font-semibold text-sm mb-1" style={{ color: '#f87171' }}>Connection failed</p>
          <p className="font-mono text-xs mb-2" style={{ color: '#f87171', opacity: 0.7 }}>{error}</p>
          <p className="font-mono text-[10px]" style={{ color: '#475569' }}>
            Start backend:{" "}
            <code style={{ color: '#64748b' }}>uvicorn api.main:app --reload --port 8000</code>
          </p>
        </div>
      )}

      {/* ── Job List ── */}
      <div className="space-y-2">
        {loading ? (
          Array.from({ length: 5 }).map((_, i) => <SkeletonCard key={i} />)
        ) : jobs.length === 0 ? (
          <div
            className="rounded-lg p-16 text-center"
            style={{ border: '1px solid var(--border)', backgroundColor: 'var(--bg-elevated)' }}
          >
            <div className="w-8 h-8 mx-auto mb-4" style={{ color: '#1e293b' }}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <rect x="2" y="7" width="20" height="14" rx="2" />
                <path d="M16 7V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v2" />
              </svg>
            </div>
            <p className="text-sm font-medium mb-1" style={{ color: '#475569' }}>No jobs found</p>
            <p className="font-mono text-[10px] tracking-wider" style={{ color: '#1e293b' }}>
              {filter
                ? `NO JOBS WITH STATUS "${filter.toUpperCase()}" — TRY ANOTHER FILTER`
                : "RUN THE SCRAPER AGENT TO POPULATE THIS LIST"}
            </p>
          </div>
        ) : (
          jobs.map(job => (
            <JobCard key={job.id} job={job} onStatusChange={handleStatusChange} />
          ))
        )}
      </div>

      {/* ── Pagination ── */}
      {!loading && totalPages > 1 && (
        <div
          className="flex items-center justify-between mt-6 pt-4"
          style={{ borderTop: '1px solid var(--border)' }}
        >
          <button
            onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            disabled={offset === 0}
            className="font-mono text-xs tracking-wider transition-colors disabled:opacity-25"
            style={{ color: '#64748b' }}
            onMouseEnter={e => e.currentTarget.style.color = 'var(--text-1)'}
            onMouseLeave={e => e.currentTarget.style.color = '#64748b'}
          >
            ← PREV
          </button>
          <span className="font-mono text-[10px] tracking-widest" style={{ color: '#334155' }}>
            PAGE {currentPage} / {totalPages}
          </span>
          <button
            onClick={() => setOffset(offset + PAGE_SIZE)}
            disabled={offset + PAGE_SIZE >= total}
            className="font-mono text-xs tracking-wider transition-colors disabled:opacity-25"
            style={{ color: '#64748b' }}
            onMouseEnter={e => e.currentTarget.style.color = 'var(--text-1)'}
            onMouseLeave={e => e.currentTarget.style.color = '#64748b'}
          >
            NEXT →
          </button>
        </div>
      )}

    </div>
  );
}
