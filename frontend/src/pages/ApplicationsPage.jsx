/**
 * ApplicationsPage.jsx — Tracks submitted applications and interview progress.
 */

import { useCallback, useEffect, useState } from "react";
import { deleteApplication, getApplications, updateApplicationTracking } from "../api/client";

// ── Tracking status config ─────────────────────────────────────────────────────
const TRACKING_STATUSES = [
  { value: "applied",   label: "Applied",   color: "#a78bfa", bg: "rgba(167,139,250,0.12)" },
  { value: "interview", label: "Interview", color: "#60a5fa", bg: "rgba(96,165,250,0.12)"  },
  { value: "offer",     label: "Offer",     color: "#34d399", bg: "rgba(52,211,153,0.12)"  },
  { value: "rejected",  label: "Rejected",  color: "#f87171", bg: "rgba(248,113,113,0.12)" },
];

const STATUS_MAP = Object.fromEntries(TRACKING_STATUSES.map(s => [s.value, s]));

const FILTER_TABS = [{ value: null, label: "All" }, ...TRACKING_STATUSES];

// ── Score badge ───────────────────────────────────────────────────────────────
function ScoreBadge({ score }) {
  if (score == null) return (
    <span className="font-mono text-xs px-2 py-0.5 rounded" style={{ background: "rgba(255,255,255,0.05)", color: "var(--text-2)", border: "1px solid var(--border)" }}>
      N/A
    </span>
  );
  const color = score >= 80 ? "#34d399" : score >= 65 ? "#fbbf24" : score >= 50 ? "#f97316" : "#f87171";
  const bg    = score >= 80 ? "rgba(52,211,153,0.12)" : score >= 65 ? "rgba(251,191,36,0.12)" : score >= 50 ? "rgba(249,115,22,0.12)" : "rgba(248,113,113,0.12)";
  return (
    <span className="font-mono text-xs px-2 py-0.5 rounded" style={{ color, background: bg, border: `1px solid ${color}40` }}>
      {score.toFixed(0)}
    </span>
  );
}

// ── Tracking status badge ─────────────────────────────────────────────────────
function TrackingBadge({ value }) {
  const cfg = STATUS_MAP[value] ?? { label: value, color: "#94a3b8", bg: "rgba(148,163,184,0.12)" };
  return (
    <span className="font-mono text-[10px] tracking-wider uppercase px-2 py-0.5 rounded" style={{ color: cfg.color, background: cfg.bg, border: `1px solid ${cfg.color}40` }}>
      {cfg.label}
    </span>
  );
}

// ── Application card ──────────────────────────────────────────────────────────
function ApplicationCard({ application, onTrackingChange, onDelete }) {
  const { job } = application;
  const [updating, setUpdating] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  async function handleStatusChange(e) {
    setUpdating(true);
    try {
      await onTrackingChange(application.id, e.target.value);
    } finally {
      setUpdating(false);
    }
  }

  async function handleDelete() {
    setDeleting(true);
    try {
      await onDelete(application.id);
    } finally {
      setDeleting(false);
      setConfirmDelete(false);
    }
  }

  const applyDate = application.applied_at
    ? new Date(application.applied_at).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })
    : "—";

  const isFailed = application.status === "failed";

  return (
    <div
      className="rounded-lg p-5 transition-all"
      style={{
        backgroundColor: "var(--bg-elevated)",
        border: `1px solid ${isFailed ? "rgba(248,113,113,0.3)" : "var(--border)"}`,
      }}
    >
      {/* Top row */}
      <div className="flex items-start justify-between gap-4 mb-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 mb-0.5 flex-wrap">
            <span className="font-mono text-[10px] tracking-[0.15em] uppercase" style={{ color: "var(--accent)" }}>
              {job?.company ?? "Unknown"}
            </span>
            {job?.source && (
              <span className="font-mono px-1.5 py-0.5 rounded" style={{ color: "var(--text-2)", border: "1px solid var(--border)", fontSize: "9px" }}>
                {job.source}
              </span>
            )}
            {isFailed && (
              <span className="font-mono px-1.5 py-0.5 rounded" style={{ color: "#f87171", border: "1px solid rgba(248,113,113,0.3)", fontSize: "9px" }}>
                Apply Failed
              </span>
            )}
          </div>
          <p className="font-semibold text-sm leading-snug" style={{ color: "var(--text-1)" }}>
            {job?.title ?? "Unknown Role"}
          </p>
          {job?.location && (
            <p className="font-mono text-[11px] mt-0.5" style={{ color: "var(--text-2)" }}>
              {job.location}
            </p>
          )}
        </div>

        <div className="flex items-center gap-2 flex-shrink-0">
          <ScoreBadge score={job?.match_score} />

          {/* Delete button */}
          {confirmDelete ? (
            <div className="flex items-center gap-1">
              <button
                onClick={handleDelete}
                disabled={deleting}
                className="font-mono text-[9px] tracking-wider uppercase px-2 py-1 rounded transition-colors"
                style={{ color: "#f87171", border: "1px solid rgba(248,113,113,0.4)", background: "rgba(248,113,113,0.08)" }}
              >
                {deleting ? "..." : "Remove"}
              </button>
              <button
                onClick={() => setConfirmDelete(false)}
                className="font-mono text-[9px] tracking-wider uppercase px-2 py-1 rounded"
                style={{ color: "var(--text-2)", border: "1px solid var(--border)" }}
              >
                Cancel
              </button>
            </div>
          ) : (
            <button
              onClick={() => setConfirmDelete(true)}
              className="p-1 rounded transition-opacity opacity-30 hover:opacity-100"
              style={{ color: "var(--text-2)" }}
              title="Remove application"
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="3 6 5 6 21 6" />
                <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
                <path d="M10 11v6M14 11v6" />
                <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
              </svg>
            </button>
          )}
        </div>
      </div>

      {/* Score reasoning */}
      {job?.match_reasoning && (
        <p className="text-xs mb-3 leading-relaxed" style={{ color: "var(--text-2)" }}>
          {job.match_reasoning}
        </p>
      )}

      {/* Error message if apply failed */}
      {isFailed && application.error_message && (
        <div className="mb-3 p-2 rounded text-xs font-mono" style={{ background: "rgba(248,113,113,0.08)", color: "#f87171", border: "1px solid rgba(248,113,113,0.2)" }}>
          {application.error_message}
        </div>
      )}

      {/* Bottom row */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-3">
          <span className="font-mono text-[11px]" style={{ color: "var(--text-2)" }}>
            Applied {applyDate}
          </span>
          <TrackingBadge value={application.tracking_status} />
        </div>

        <div className="flex items-center gap-2">
          {job?.source_url && (
            <a
              href={job.source_url}
              target="_blank"
              rel="noopener noreferrer"
              className="font-mono text-[10px] tracking-wider uppercase px-2.5 py-1 rounded transition-colors"
              style={{ color: "var(--text-2)", border: "1px solid var(--border)" }}
              onMouseEnter={e => { e.currentTarget.style.color = "var(--accent)"; e.currentTarget.style.borderColor = "var(--accent-border)"; }}
              onMouseLeave={e => { e.currentTarget.style.color = "var(--text-2)"; e.currentTarget.style.borderColor = "var(--border)"; }}
            >
              View Posting ↗
            </a>
          )}

          {/* Tracking status dropdown */}
          <div className="relative">
            <select
              value={application.tracking_status}
              onChange={handleStatusChange}
              disabled={updating}
              className="font-mono text-[10px] tracking-wider uppercase pl-2.5 pr-6 py-1 rounded appearance-none cursor-pointer outline-none"
              style={{
                backgroundColor: "var(--bg-base)",
                border: "1px solid var(--accent-border)",
                color: "var(--accent)",
                opacity: updating ? 0.5 : 1,
              }}
            >
              {TRACKING_STATUSES.map(s => (
                <option key={s.value} value={s.value} style={{ background: "#0f172a", color: "#e2e8f0" }}>
                  {s.label}
                </option>
              ))}
            </select>
            <div className="pointer-events-none absolute right-1.5 top-1/2 -translate-y-1/2" style={{ color: "var(--accent)" }}>
              <svg width="8" height="8" viewBox="0 0 8 8" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round">
                <path d="M0 2.5l4 3 4-3" />
              </svg>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────
export function ApplicationsPage() {
  const [applications, setApplications] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [activeTab, setActiveTab] = useState(null);
  const [offset, setOffset] = useState(0);
  const LIMIT = 20;

  const fetchApplications = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getApplications({ tracking_status: activeTab, limit: LIMIT, offset });
      setApplications(data.applications);
      setTotal(data.total);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [activeTab, offset]);

  useEffect(() => { fetchApplications(); }, [fetchApplications]);

  function handleTabChange(tabValue) {
    setActiveTab(tabValue);
    setOffset(0);
  }

  async function handleTrackingChange(applicationId, newStatus) {
    try {
      const updated = await updateApplicationTracking(applicationId, newStatus);
      setApplications(prev => prev.map(app => app.id === updated.id ? updated : app));
    } catch (err) {
      console.error("Failed to update tracking status:", err);
    }
  }

  async function handleDelete(applicationId) {
    try {
      await deleteApplication(applicationId);
      // Remove from local state immediately — no refetch needed
      setApplications(prev => prev.filter(app => app.id !== applicationId));
      setTotal(prev => prev - 1);
    } catch (err) {
      console.error("Failed to delete application:", err);
    }
  }

  const totalPages = Math.ceil(total / LIMIT);
  const currentPage = Math.floor(offset / LIMIT) + 1;

  return (
    <div className="p-8 max-w-4xl">

      {/* Header */}
      <div className="mb-6">
        <p className="font-mono text-[10px] tracking-[0.2em] uppercase mb-1" style={{ color: "var(--accent)" }}>
          Submission Log
        </p>
        <div className="flex items-baseline gap-3">
          <h1 className="text-2xl font-bold tracking-tight" style={{ color: "var(--text-1)" }}>
            Applications
          </h1>
          {total > 0 && (
            <span className="font-mono text-sm" style={{ color: "var(--text-2)" }}>
              {total} total
            </span>
          )}
        </div>
        <p className="text-sm mt-1" style={{ color: "var(--text-2)" }}>
          Track where you are in each hiring process.
        </p>
      </div>

      {/* Filter tabs */}
      <div className="flex items-center gap-1 mb-6 flex-wrap">
        {FILTER_TABS.map(tab => {
          const isActive = activeTab === tab.value;
          return (
            <button
              key={tab.value ?? "all"}
              onClick={() => handleTabChange(tab.value)}
              className="font-mono text-[10px] tracking-wider uppercase px-3 py-1.5 rounded transition-colors"
              style={{
                color: isActive ? (tab.color ?? "var(--accent)") : "var(--text-2)",
                backgroundColor: isActive ? (tab.bg ?? "rgba(167,139,250,0.12)") : "transparent",
                border: `1px solid ${isActive ? (tab.color ?? "var(--accent)") + "60" : "var(--border)"}`,
              }}
            >
              {tab.label}
            </button>
          );
        })}
      </div>

      {/* Content */}
      {loading ? (
        <div className="flex items-center gap-2 py-12" style={{ color: "var(--text-2)" }}>
          <div className="w-4 h-4 border-2 rounded-full animate-spin" style={{ borderColor: "var(--accent)", borderTopColor: "transparent" }} />
          <span className="font-mono text-xs tracking-wider">Loading applications...</span>
        </div>
      ) : error ? (
        <div className="p-4 rounded-lg font-mono text-xs" style={{ background: "rgba(248,113,113,0.08)", color: "#f87171", border: "1px solid rgba(248,113,113,0.2)" }}>
          Failed to load: {error}
        </div>
      ) : applications.length === 0 ? (
        <EmptyState activeTab={activeTab} />
      ) : (
        <>
          <div className="flex flex-col gap-3">
            {applications.map(app => (
              <ApplicationCard
                key={app.id}
                application={app}
                onTrackingChange={handleTrackingChange}
                onDelete={handleDelete}
              />
            ))}
          </div>

          {totalPages > 1 && (
            <div className="flex items-center justify-between mt-6 pt-4" style={{ borderTop: "1px solid var(--border)" }}>
              <span className="font-mono text-xs" style={{ color: "var(--text-2)" }}>
                Page {currentPage} of {totalPages}
              </span>
              <div className="flex gap-2">
                <button
                  onClick={() => setOffset(o => Math.max(0, o - LIMIT))}
                  disabled={offset === 0}
                  className="font-mono text-[10px] tracking-wider uppercase px-3 py-1.5 rounded disabled:opacity-30"
                  style={{ color: "var(--text-2)", border: "1px solid var(--border)" }}
                >
                  ← Prev
                </button>
                <button
                  onClick={() => setOffset(o => o + LIMIT)}
                  disabled={offset + LIMIT >= total}
                  className="font-mono text-[10px] tracking-wider uppercase px-3 py-1.5 rounded disabled:opacity-30"
                  style={{ color: "var(--text-2)", border: "1px solid var(--border)" }}
                >
                  Next →
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ── Empty state ───────────────────────────────────────────────────────────────
function EmptyState({ activeTab }) {
  const cfg = activeTab ? STATUS_MAP[activeTab] : null;
  return (
    <div className="rounded-lg p-16 text-center" style={{ border: "1px solid var(--border)", backgroundColor: "var(--bg-elevated)" }}>
      <div className="w-8 h-8 mx-auto mb-4" style={{ color: "var(--accent)", opacity: 0.4 }}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
          <polyline points="14 2 14 8 20 8" />
          <line x1="9" y1="13" x2="15" y2="13" />
          <line x1="9" y1="17" x2="13" y2="17" />
        </svg>
      </div>
      {cfg ? (
        <>
          <p className="text-sm font-medium mb-1" style={{ color: "var(--text-1)" }}>
            No <span style={{ color: cfg.color }}>{cfg.label}</span> applications
          </p>
          <p className="font-mono text-[10px] tracking-wider" style={{ color: "var(--text-2)" }}>
            Update the dropdown on any card to move it here
          </p>
        </>
      ) : (
        <>
          <p className="text-sm font-medium mb-1" style={{ color: "var(--text-1)" }}>No applications yet</p>
          <p className="font-mono text-[10px] tracking-wider" style={{ color: "var(--text-2)" }}>
            Run the Orchestrator to start submitting applications automatically
          </p>
        </>
      )}
    </div>
  );
}
