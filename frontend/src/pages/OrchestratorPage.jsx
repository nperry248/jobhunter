/**
 * OrchestratorPage.jsx — The Phase 4 Orchestrator control panel.
 *
 * WHAT THIS PAGE DOES:
 *   Lets the user start an AI agent session with a natural-language goal.
 *   The agent reasons through the goal (scrape → score → review → apply)
 *   and pauses for human approval before submitting any applications.
 *
 * FOUR STATES (driven by `status`):
 *   1. idle      — Goal input form, dry run toggle, Start button
 *   2. running   — Pulsing indicator + live reasoning log (polls every 5s)
 *   3. waiting   — Approval panel: job cards + "Approve & Apply" / "Cancel"
 *   4. done      — Result summary + token usage + "Start New Session"
 *
 * CONCEPT — Polling vs WebSockets:
 *   Polling: client asks "what's the status?" every N seconds.
 *   WebSockets: server pushes updates as they happen.
 *   For this use case (one session at a time, low frequency updates) polling is
 *   simpler and more than adequate. We poll every 5s while status === "running".
 */

import { useEffect, useRef, useState } from "react";
import {
  approveOrchestrator,
  getOrchestratorStatus,
  startOrchestrator,
} from "../api/client";

// ── Score badge ────────────────────────────────────────────────────────────────
function ScoreBadge({ score }) {
  if (score == null) return null;
  const rounded = Math.round(score);
  const style =
    rounded >= 80
      ? { color: '#34d399', backgroundColor: 'rgba(52,211,153,0.1)', border: '1px solid rgba(52,211,153,0.25)' }
      : rounded >= 60
      ? { color: '#fbbf24', backgroundColor: 'rgba(251,191,36,0.1)', border: '1px solid rgba(251,191,36,0.25)' }
      : { color: '#f87171', backgroundColor: 'rgba(248,113,113,0.1)', border: '1px solid rgba(248,113,113,0.25)' };
  return (
    <span className="font-mono text-[10px] font-semibold px-2 py-0.5 rounded" style={style}>
      {rounded}%
    </span>
  );
}

// ── Human-readable step descriptions ──────────────────────────────────────────
// Converts a raw {tool, result, error} step into a plain-English sentence.
function formatStep(step) {
  const r = step.result || {};

  if (step.error) {
    const firstLine = step.error.split("\n")[0];
    return { icon: "ERR", text: firstLine, isError: true };
  }

  switch (step.tool) {
    case "check_db_state": {
      const { total = 0, new: newJobs = 0, scored = 0, reviewed = 0, applied = 0 } = r;
      if (total === 0) return { icon: "DB", text: "Database is empty — no jobs scraped yet." };
      const parts = [];
      if (newJobs > 0) parts.push(`${newJobs} unscored`);
      if (scored > 0) parts.push(`${scored} scored`);
      if (reviewed > 0) parts.push(`${reviewed} reviewed`);
      if (applied > 0) parts.push(`${applied} applied`);
      return { icon: "DB", text: `Database has ${total} jobs — ${parts.join(", ")}.` };
    }

    case "scrape_jobs": {
      const { new_jobs = 0, duplicates = 0 } = r;
      if (new_jobs === 0) return { icon: "NET", text: `No new jobs found (${duplicates} duplicates skipped).` };
      return { icon: "NET", text: `Scraped ${new_jobs} new job listings${duplicates ? ` (${duplicates} dupes skipped)` : ""}.` };
    }

    case "score_jobs": {
      const { scored = 0, failed = 0 } = r;
      if (scored === 0) return { icon: "AI", text: "No unscored jobs to score." };
      return { icon: "AI", text: `Scored ${scored} job${scored !== 1 ? "s" : ""} against your resume${failed ? ` (${failed} failed)` : ""}.` };
    }

    case "auto_review_jobs": {
      const { reviewed = 0, min_score = 70 } = r;
      if (reviewed === 0) return { icon: "OK", text: `No scored jobs above ${min_score}% to auto-approve.` };
      return { icon: "OK", text: `Auto-approved ${reviewed} job${reviewed !== 1 ? "s" : ""} above ${min_score}%.` };
    }

    case "get_reviewed_jobs": {
      const jobs = r.jobs || [];
      if (jobs.length === 0) return { icon: "OK", text: "No reviewed jobs found above the score threshold." };
      const list = jobs.map((j) => `${j.company} (${Math.round(j.score)}%)`).join(", ");
      return { icon: "OK", text: `Found ${jobs.length} reviewed job${jobs.length !== 1 ? "s" : ""}: ${list}.` };
    }

    case "request_apply_approval": {
      const count = (r.job_ids || []).length;
      return { icon: "HLT", text: `Paused — requesting approval to apply to ${count} job${count !== 1 ? "s" : ""}.` };
    }

    case "apply_jobs": {
      const { total_applied = 0, total_dry_run = 0, total_failed = 0, total_attempted = 0, errors = [] } = r;
      if (total_dry_run > 0)
        return { icon: "DRY", text: `Dry run: filled ${total_dry_run} form${total_dry_run !== 1 ? "s" : ""} (not submitted).` };
      const parts = [];
      if (total_applied > 0) parts.push(`${total_applied} submitted`);
      if (total_failed > 0) parts.push(`${total_failed} failed`);
      const summary = `Applied to ${total_attempted} job${total_attempted !== 1 ? "s" : ""} — ${parts.join(", ") || "none"}.`;
      const errorDetail = errors.length > 0 ? ` Error: ${errors[0].split(":").slice(-1)[0].trim()}` : "";
      return { icon: "OUT", text: summary + errorDetail, isError: total_applied === 0 && total_failed > 0 };
    }

    default:
      return { icon: "·", text: step.tool };
  }
}

// ── Step entry in the reasoning log ───────────────────────────────────────────
// `index` prop is used to stagger the fadeInUp animation — each row enters
// slightly after the previous one, creating a cascading effect.
function StepRow({ step, index }) {
  const { icon, text, isError } = formatStep(step);
  return (
    <div
      className="flex gap-3 py-2.5 items-start animate-fade-in-up"
      style={{
        borderBottom: '1px solid rgba(255,255,255,0.04)',
        animationDelay: `${Math.min(index * 40, 400)}ms`,
        opacity: 0, // starts invisible, animation fills it in
      }}
    >
      {/* Icon badge — monospace tag shows the tool type at a glance */}
      <span
        className="font-mono text-[9px] font-semibold tracking-widest shrink-0 px-1.5 py-0.5 rounded mt-0.5"
        style={{
          color: isError ? '#f87171' : '#a78bfa',
          backgroundColor: isError ? 'rgba(248,113,113,0.1)' : 'rgba(167,139,250,0.1)',
          minWidth: '36px',
          textAlign: 'center',
        }}
      >
        {icon}
      </span>
      <span
        className="text-sm leading-5"
        style={{ color: isError ? '#f87171' : 'var(--text-1)' }}
      >
        {text}
      </span>
    </div>
  );
}

// ── Pending job card in the approval panel ────────────────────────────────────
function PendingJobCard({ job, onDismiss }) {
  return (
    <div
      className="flex items-center justify-between gap-3 px-4 py-3 rounded-lg"
      style={{
        backgroundColor: 'var(--bg-elevated)',
        border: '1px solid var(--border)',
      }}
    >
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className="text-sm font-semibold truncate" style={{ color: 'var(--text-1)' }}>
            {job.title}
          </p>
          {job.url && (
            <a
              href={job.url}
              target="_blank"
              rel="noreferrer"
              className="shrink-0 font-mono text-[10px] tracking-wider transition-colors"
              style={{ color: '#475569' }}
              onMouseEnter={e => e.currentTarget.style.color = '#a78bfa'}
              onMouseLeave={e => e.currentTarget.style.color = '#475569'}
            >
              VIEW ↗
            </a>
          )}
        </div>
        <p className="font-mono text-[10px] tracking-wider mt-0.5" style={{ color: '#475569' }}>
          {job.company}
        </p>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <ScoreBadge score={job.score} />
        <button
          onClick={() => onDismiss(job.id)}
          title="Remove from apply list"
          className="w-6 h-6 flex items-center justify-center rounded font-mono text-xs transition-all"
          style={{ color: '#334155' }}
          onMouseEnter={e => { e.currentTarget.style.color = '#f87171'; e.currentTarget.style.backgroundColor = 'rgba(248,113,113,0.1)'; }}
          onMouseLeave={e => { e.currentTarget.style.color = '#334155'; e.currentTarget.style.backgroundColor = 'transparent'; }}
        >
          ✕
        </button>
      </div>
    </div>
  );
}

// ── Toggle switch ─────────────────────────────────────────────────────────────
function Toggle({ checked, onChange }) {
  return (
    <div
      onClick={onChange}
      className="relative w-9 h-5 rounded-full transition-colors cursor-pointer"
      style={{ backgroundColor: checked ? '#a78bfa' : 'rgba(255,255,255,0.08)' }}
    >
      <div
        className="absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform"
        style={{ left: '2px', transform: checked ? 'translateX(16px)' : 'translateX(0)' }}
      />
    </div>
  );
}

// ── Main page ──────────────────────────────────────────────────────────────────
export function OrchestratorPage({
  sessionId,
  setSessionId,
  sessionData,
  setSessionData,
  dryRun,
  setDryRun,
  mode,
  setMode,
  handoff,
  setHandoff,
  maxApply,
  setMaxApply,
}) {
  const [goal, setGoal] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [dismissedIds, setDismissedIds] = useState(new Set());
  const pollRef = useRef(null);

  // ── Derived status ─────────────────────────────────────────────────────────
  const status = sessionData?.status ?? "idle";
  const isRunning = status === "running";
  const isWaiting = status === "waiting_for_approval";
  const isDone    = status === "complete" || status === "failed";

  // ── Polling ────────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!sessionId || isDone || isWaiting) {
      clearInterval(pollRef.current);
      return;
    }

    const poll = async () => {
      try {
        const data = await getOrchestratorStatus(sessionId);
        setSessionData(data);
      } catch (err) {
        console.warn("Poll error:", err);
      }
    };

    poll();
    pollRef.current = setInterval(poll, 5000);
    return () => clearInterval(pollRef.current);
  }, [sessionId, isDone, isWaiting]);

  // ── Handlers ───────────────────────────────────────────────────────────────

  const handleStart = async () => {
    if (!goal.trim()) return;
    setError(null);
    setLoading(true);
    setSessionData(null);

    try {
      const { session_id } = await startOrchestrator(goal.trim(), dryRun, mode, handoff, maxApply);
      setSessionId(session_id);
      setSessionData({
        session_id,
        status: "running",
        goal: goal.trim(),
        steps: [],
        pending_jobs: [],
        token_usage: 0,
        result_summary: null,
      });
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleDismiss = (jobId) => {
    setDismissedIds((prev) => new Set([...prev, jobId]));
  };

  const handleApprove = async () => {
    if (!sessionId) return;
    setError(null);

    const visibleIds = (sessionData?.pending_jobs ?? [])
      .filter((j) => !dismissedIds.has(j.id))
      .map((j) => j.id);

    if (visibleIds.length === 0) {
      setError("No jobs selected — remove the ✕ on at least one job to apply.");
      return;
    }

    try {
      await approveOrchestrator(sessionId, visibleIds);
      clearInterval(pollRef.current);
      setSessionData((prev) => ({ ...prev, status: "running" }));
    } catch (err) {
      setError(err.message);
    }
  };

  const handleCancel = () => {
    clearInterval(pollRef.current);
    setSessionData((prev) => ({ ...prev, status: "failed", result_summary: "Cancelled by user." }));
  };

  const handleNewSession = () => {
    clearInterval(pollRef.current);
    setSessionId(null);
    setSessionData(null);
    setDryRun(false);
    setMode("fresh_scan");
    setHandoff(false);
    setMaxApply(5);
    setGoal("");
    setError(null);
    setDismissedIds(new Set());
  };

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div className="p-8 max-w-3xl">

      {/* Header */}
      <div className="mb-8">
        <p className="font-mono text-[10px] tracking-[0.2em] uppercase mb-1" style={{ color: '#334155' }}>
          AI Agent
        </p>
        <h1 className="text-2xl font-bold tracking-tight" style={{ color: 'var(--text-1)' }}>
          Orchestrator
        </h1>
        <p className="text-sm mt-1" style={{ color: 'var(--text-2)' }}>
          Autonomous agent that scrapes, scores, and prepares applications — pausing for your approval before submitting.
        </p>
      </div>

      {/* ── State 1: Idle — goal input ─────────────────────────────────────── */}
      {!sessionId && (
        <div className="space-y-6">

          {/* Mode selector */}
          <div>
            <label className="block font-mono text-[10px] tracking-widest uppercase mb-2" style={{ color: '#475569' }}>
              Mode
            </label>
            <div className="grid grid-cols-2 gap-2">
              {[
                {
                  value: "fresh_scan",
                  label: "Fresh Scan",
                  description: "Scrape new jobs, score them, and prepare applications from scratch.",
                },
                {
                  value: "use_reviewed",
                  label: "Use Reviewed",
                  description: "Apply to jobs you've already reviewed and approved in the Jobs tab.",
                },
              ].map(({ value, label, description }) => (
                <button
                  key={value}
                  onClick={() => setMode(value)}
                  className="text-left px-4 py-3 rounded-lg transition-all"
                  style={mode === value
                    ? {
                        border: `1px solid var(--accent-border)`,
                        backgroundColor: 'var(--accent-bg)',
                        color: 'var(--text-1)',
                      }
                    : {
                        border: '1px solid var(--border)',
                        backgroundColor: 'var(--bg-elevated)',
                        color: '#64748b',
                      }
                  }
                >
                  <p className="text-sm font-semibold mb-0.5">{label}</p>
                  <p className="text-xs leading-relaxed" style={{ color: '#475569' }}>{description}</p>
                </button>
              ))}
            </div>
          </div>

          {/* Max applications */}
          {/* NOTE: This cap controls how many jobs the agent will prepare per session.
              Default 5 prevents accidentally submitting a huge batch during testing. */}
          <div>
            <label className="block font-mono text-[10px] tracking-widest uppercase mb-2" style={{ color: '#475569' }}>
              Max Applications
            </label>
            <div className="flex items-center gap-3">
              <input
                type="number"
                min={1}
                max={10}
                value={maxApply}
                onChange={(e) =>
                  setMaxApply(Math.min(10, Math.max(1, parseInt(e.target.value, 10) || 1)))
                }
                className="w-20 rounded-lg px-3 py-2 text-sm font-mono text-center focus:outline-none transition-colors"
                style={{
                  backgroundColor: 'var(--bg-input)',
                  border: '1px solid var(--border)',
                  color: 'var(--text-1)',
                }}
                onFocus={e => e.currentTarget.style.borderColor = 'var(--accent-border)'}
                onBlur={e => e.currentTarget.style.borderColor = 'var(--border)'}
              />
              <span className="font-mono text-[10px] tracking-wider" style={{ color: '#334155' }}>
                AGENT PREPARES AT MOST THIS MANY PER SESSION (1–10)
              </span>
            </div>
          </div>

          {/* Goal input */}
          <div>
            <label className="block font-mono text-[10px] tracking-widest uppercase mb-2" style={{ color: '#475569' }}>
              Goal
            </label>
            <textarea
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleStart();
              }}
              placeholder={
                mode === "fresh_scan"
                  ? "e.g. Find me good SWE jobs and prepare applications"
                  : "e.g. Apply to all jobs I've already reviewed"
              }
              rows={3}
              className="w-full rounded-lg px-4 py-3 text-sm resize-none focus:outline-none transition-colors"
              style={{
                backgroundColor: 'var(--bg-input)',
                border: '1px solid var(--border)',
                color: 'var(--text-1)',
              }}
              onFocus={e => e.currentTarget.style.borderColor = 'var(--accent-border)'}
              onBlur={e => e.currentTarget.style.borderColor = 'var(--border)'}
            />
          </div>

          {/* Toggles */}
          <div className="space-y-3">
            <label className="flex items-center gap-3 cursor-pointer select-none">
              <Toggle checked={handoff} onChange={() => setHandoff(v => !v)} />
              <span className="text-sm" style={{ color: '#64748b' }}>
                Handoff mode{" "}
                <span className="font-mono text-[10px] tracking-wider" style={{ color: '#334155' }}>
                  — fill forms in a visible browser, you submit
                </span>
              </span>
            </label>

            <label className="flex items-center gap-3 cursor-pointer select-none">
              <Toggle checked={dryRun} onChange={() => setDryRun(v => !v)} />
              <span className="text-sm" style={{ color: '#64748b' }}>
                Dry run{" "}
                <span className="font-mono text-[10px] tracking-wider" style={{ color: '#334155' }}>
                  — fill forms + screenshot, never submit
                </span>
              </span>
            </label>
          </div>

          {error && (
            <p className="text-sm font-mono" style={{ color: '#f87171' }}>{error}</p>
          )}

          <div className="flex items-center gap-4">
            <button
              onClick={handleStart}
              disabled={loading || !goal.trim()}
              className="px-6 py-2.5 rounded-lg text-sm font-semibold transition-all"
              style={{
                backgroundColor: '#a78bfa',
                color: '#07090c',
                opacity: (loading || !goal.trim()) ? 0.4 : 1,
                cursor: (loading || !goal.trim()) ? 'not-allowed' : 'pointer',
              }}
              onMouseEnter={e => { if (!loading && goal.trim()) e.currentTarget.style.backgroundColor = '#c4b5fd'; }}
              onMouseLeave={e => { e.currentTarget.style.backgroundColor = '#a78bfa'; }}
            >
              {loading ? "Starting…" : "Start Agent"}
            </button>
            <span className="font-mono text-[10px] tracking-wider" style={{ color: '#334155' }}>
              ⌘↵ TO LAUNCH
            </span>
          </div>
        </div>
      )}

      {/* ── State 2: Running — reasoning log ──────────────────────────────── */}
      {sessionId && isRunning && (
        <div className="space-y-5">

          {/* Live status indicator with ring pulse */}
          <div className="flex items-center gap-3">
            <div className="relative w-3 h-3 shrink-0">
              <div className="absolute inset-0 rounded-full bg-emerald-500 animate-ring-pulse" />
              <div className="absolute inset-0 rounded-full bg-emerald-500" />
            </div>
            <span className="text-sm" style={{ color: 'var(--text-1)' }}>
              {(() => {
                const steps = sessionData?.steps ?? [];
                const lastStep = steps[steps.length - 1];
                if (lastStep?.tool === "request_apply_approval") return "Submitting applications…";
                if (lastStep) return formatStep(lastStep).text;
                return "Initializing…";
              })()}
            </span>
            <span className="font-mono text-[10px] tracking-wider ml-auto" style={{ color: '#334155' }}>
              {sessionData?.token_usage ?? 0} TOKENS
            </span>
          </div>

          {/* Reasoning log — terminal-style panel */}
          <div
            className="rounded-lg p-4 max-h-96 overflow-y-auto"
            style={{
              backgroundColor: 'var(--bg-elevated)',
              border: '1px solid var(--border)',
            }}
          >
            <p className="font-mono text-[10px] tracking-widest uppercase mb-3" style={{ color: '#334155' }}>
              Agent Reasoning Log
            </p>
            {sessionData?.steps?.length === 0 ? (
              <p className="font-mono text-[10px] tracking-wider" style={{ color: '#334155' }}>
                WAITING FOR FIRST TOOL CALL…
              </p>
            ) : (
              sessionData?.steps?.map((step, i) => (
                <StepRow key={i} step={step} index={i} />
              ))
            )}
          </div>

          <button
            onClick={handleCancel}
            className="font-mono text-[10px] tracking-wider transition-colors"
            style={{ color: '#334155' }}
            onMouseEnter={e => e.currentTarget.style.color = '#64748b'}
            onMouseLeave={e => e.currentTarget.style.color = '#334155'}
          >
            CANCEL SESSION
          </button>
        </div>
      )}

      {/* ── State 3: Waiting for approval ─────────────────────────────────── */}
      {sessionId && isWaiting && (
        <div className="space-y-5">

          {/* Pause indicator */}
          <div
            className="flex items-center gap-3 px-4 py-3 rounded-lg"
            style={{
              backgroundColor: 'rgba(251,191,36,0.05)',
              border: '1px solid rgba(251,191,36,0.2)',
            }}
          >
            <div className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: '#fbbf24' }} />
            <span className="text-sm font-semibold" style={{ color: '#fbbf24' }}>
              Paused — review these jobs before applying
            </span>
          </div>

          {/* Reasoning log (collapsible) */}
          {sessionData?.steps?.length > 0 && (
            <details className="group">
              <summary
                className="font-mono text-[10px] tracking-wider cursor-pointer list-none transition-colors"
                style={{ color: '#475569' }}
                onMouseEnter={e => e.currentTarget.style.color = '#64748b'}
                onMouseLeave={e => e.currentTarget.style.color = '#475569'}
              >
                SHOW REASONING LOG ({sessionData.steps.length} STEPS) ▸
              </summary>
              <div
                className="mt-2 rounded-lg p-4 max-h-48 overflow-y-auto"
                style={{ backgroundColor: 'var(--bg-elevated)', border: '1px solid var(--border)' }}
              >
                {sessionData.steps.map((step, i) => (
                  <StepRow key={i} step={step} index={i} />
                ))}
              </div>
            </details>
          )}

          {/* Pending job cards */}
          {(() => {
            const allJobs = sessionData?.pending_jobs ?? [];
            const visibleJobs = allJobs.filter((j) => !dismissedIds.has(j.id));
            const dismissedCount = allJobs.length - visibleJobs.length;
            return (
              <>
                <div className="space-y-2">
                  {allJobs.length === 0 ? (
                    <p className="text-sm" style={{ color: '#475569' }}>No jobs pending approval.</p>
                  ) : (
                    visibleJobs.map((job) => (
                      <PendingJobCard key={job.id} job={job} onDismiss={handleDismiss} />
                    ))
                  )}
                  {dismissedCount > 0 && (
                    <p className="font-mono text-[10px] tracking-wider pt-1" style={{ color: '#475569' }}>
                      {dismissedCount} JOB{dismissedCount !== 1 ? "S" : ""} REMOVED —{" "}
                      <button
                        onClick={() => setDismissedIds(new Set())}
                        className="underline transition-colors"
                        onMouseEnter={e => e.currentTarget.style.color = '#64748b'}
                        onMouseLeave={e => {}}
                      >
                        UNDO
                      </button>
                    </p>
                  )}
                </div>

                {error && (
                  <p className="text-sm font-mono" style={{ color: '#f87171' }}>{error}</p>
                )}

                <div className="flex gap-3">
                  <button
                    onClick={handleApprove}
                    disabled={visibleJobs.length === 0}
                    className="px-5 py-2.5 rounded-lg text-sm font-semibold transition-all"
                    style={{
                      backgroundColor: visibleJobs.length === 0 ? 'rgba(52,211,153,0.2)' : '#34d399',
                      color: '#07090c',
                      opacity: visibleJobs.length === 0 ? 0.4 : 1,
                      cursor: visibleJobs.length === 0 ? 'not-allowed' : 'pointer',
                    }}
                    onMouseEnter={e => { if (visibleJobs.length > 0) e.currentTarget.style.backgroundColor = '#6ee7b7'; }}
                    onMouseLeave={e => { if (visibleJobs.length > 0) e.currentTarget.style.backgroundColor = '#34d399'; }}
                  >
                    Approve & Apply ({visibleJobs.length} job{visibleJobs.length !== 1 ? "s" : ""})
                  </button>
                  <button
                    onClick={handleCancel}
                    className="px-5 py-2.5 rounded-lg text-sm font-semibold transition-all"
                    style={{ backgroundColor: 'rgba(255,255,255,0.06)', color: '#64748b' }}
                    onMouseEnter={e => e.currentTarget.style.backgroundColor = 'rgba(255,255,255,0.1)'}
                    onMouseLeave={e => e.currentTarget.style.backgroundColor = 'rgba(255,255,255,0.06)'}
                  >
                    Cancel
                  </button>
                </div>

                {dryRun && (
                  <p className="font-mono text-[10px] tracking-wider" style={{ color: '#334155' }}>
                    DRY RUN MODE — FORMS WILL BE FILLED BUT NOT SUBMITTED
                  </p>
                )}
              </>
            );
          })()}
        </div>
      )}

      {/* ── State 4: Complete / Failed ─────────────────────────────────────── */}
      {sessionId && isDone && (
        <div className="space-y-5">

          <div className="flex items-center gap-2">
            <div
              className="w-2 h-2 rounded-full"
              style={{ backgroundColor: status === "complete" ? '#34d399' : '#f87171' }}
            />
            <span
              className="text-sm font-semibold"
              style={{ color: status === "complete" ? '#34d399' : '#f87171' }}
            >
              {status === "complete" ? "Session complete" : "Session failed"}
            </span>
          </div>

          {/* Result summary */}
          {sessionData?.result_summary && (
            <div
              className="rounded-lg p-4"
              style={{ backgroundColor: 'var(--bg-elevated)', border: '1px solid var(--border)' }}
            >
              <p className="font-mono text-[10px] tracking-widest uppercase mb-2" style={{ color: '#334155' }}>
                Summary
              </p>
              <p className="text-sm leading-relaxed" style={{ color: 'var(--text-1)' }}>
                {sessionData.result_summary}
              </p>
            </div>
          )}

          {/* Token stats */}
          <div className="flex gap-6">
            <span className="font-mono text-[10px] tracking-wider" style={{ color: '#334155' }}>
              TOKENS: <span style={{ color: '#64748b' }}>{sessionData?.token_usage ?? 0}</span>
            </span>
            <span className="font-mono text-[10px] tracking-wider" style={{ color: '#334155' }}>
              STEPS: <span style={{ color: '#64748b' }}>{sessionData?.steps?.length ?? 0}</span>
            </span>
          </div>

          {/* Full reasoning log */}
          {sessionData?.steps?.length > 0 && (
            <details>
              <summary
                className="font-mono text-[10px] tracking-wider cursor-pointer list-none transition-colors"
                style={{ color: '#475569' }}
                onMouseEnter={e => e.currentTarget.style.color = '#64748b'}
                onMouseLeave={e => e.currentTarget.style.color = '#475569'}
              >
                SHOW FULL REASONING LOG ▸
              </summary>
              <div
                className="mt-2 rounded-lg p-4 max-h-64 overflow-y-auto"
                style={{ backgroundColor: 'var(--bg-elevated)', border: '1px solid var(--border)' }}
              >
                {sessionData.steps.map((step, i) => (
                  <StepRow key={i} step={step} index={i} />
                ))}
              </div>
            </details>
          )}

          <button
            onClick={handleNewSession}
            className="px-6 py-2.5 rounded-lg text-sm font-semibold transition-all"
            style={{ backgroundColor: '#a78bfa', color: '#07090c' }}
            onMouseEnter={e => e.currentTarget.style.backgroundColor = '#c4b5fd'}
            onMouseLeave={e => e.currentTarget.style.backgroundColor = '#a78bfa'}
          >
            Start New Session
          </button>
        </div>
      )}
    </div>
  );
}
