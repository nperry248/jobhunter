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
 *   2. running   — Pulsing indicator + live reasoning log (polls every 2s)
 *   3. waiting   — Approval panel: job cards + "Approve & Apply" / "Cancel"
 *   4. done      — Result summary + token usage + "Start New Session"
 *
 * CONCEPT — Polling vs WebSockets:
 *   Polling: client asks "what's the status?" every N seconds.
 *   WebSockets: server pushes updates as they happen.
 *   For this use case (one session at a time, low frequency updates) polling is
 *   simpler and more than adequate. We poll every 2s while status === "running",
 *   and stop polling when the session is no longer in a transient state.
 */

import { useEffect, useRef, useState } from "react";
import {
  approveOrchestrator,
  getOrchestratorStatus,
  startOrchestrator,
} from "../api/client";

// ── Score badge ────────────────────────────────────────────────────────────────
// Reused from JobsPage: green (80+), yellow (60-79), red (<60)
function ScoreBadge({ score }) {
  if (score == null) return null;
  const rounded = Math.round(score);
  const color =
    rounded >= 80
      ? "bg-emerald-500/15 text-emerald-400 border border-emerald-500/30"
      : rounded >= 60
      ? "bg-yellow-500/15 text-yellow-400 border border-yellow-500/30"
      : "bg-red-500/15 text-red-400 border border-red-500/30";
  return (
    <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${color}`}>
      {rounded}%
    </span>
  );
}

// ── Human-readable step descriptions ──────────────────────────────────────────
// Converts a raw {tool, result, error} step into a plain-English sentence.
// Each tool has its own formatter that pulls the relevant numbers/names out of
// the result dict so the user sees "Scored 12 jobs" not a JSON blob.
function formatStep(step) {
  const r = step.result || {};

  if (step.error) {
    // Strip the long Playwright call log — just show the first line
    const firstLine = step.error.split("\n")[0];
    return { icon: "✗", text: firstLine, isError: true };
  }

  switch (step.tool) {
    case "check_db_state": {
      const { total = 0, new: newJobs = 0, scored = 0, reviewed = 0, applied = 0 } = r;
      if (total === 0) return { icon: "🔍", text: "Database is empty — no jobs scraped yet." };
      const parts = [];
      if (newJobs > 0) parts.push(`${newJobs} unscored`);
      if (scored > 0) parts.push(`${scored} scored`);
      if (reviewed > 0) parts.push(`${reviewed} reviewed`);
      if (applied > 0) parts.push(`${applied} applied`);
      return { icon: "🔍", text: `Database has ${total} jobs — ${parts.join(", ")}.` };
    }

    case "scrape_jobs": {
      const { new_jobs = 0, duplicates = 0 } = r;
      if (new_jobs === 0) return { icon: "📋", text: `No new jobs found (${duplicates} duplicates skipped).` };
      return { icon: "📋", text: `Scraped ${new_jobs} new job listings${duplicates ? ` (${duplicates} duplicates skipped)` : ""}.` };
    }

    case "score_jobs": {
      const { scored = 0, failed = 0 } = r;
      if (scored === 0) return { icon: "⭐", text: "No unscored jobs to score." };
      return { icon: "⭐", text: `Scored ${scored} job${scored !== 1 ? "s" : ""} against your resume${failed ? ` (${failed} failed)` : ""}.` };
    }

    case "auto_review_jobs": {
      const { reviewed = 0, min_score = 70 } = r;
      if (reviewed === 0) return { icon: "✓", text: `No scored jobs above ${min_score}% to auto-approve.` };
      return { icon: "✓", text: `Auto-approved ${reviewed} job${reviewed !== 1 ? "s" : ""} scoring above ${min_score}%.` };
    }

    case "get_reviewed_jobs": {
      const jobs = r.jobs || [];
      if (jobs.length === 0) return { icon: "👀", text: "No reviewed jobs found above the score threshold." };
      const list = jobs.map((j) => `${j.company} (${Math.round(j.score)}%)`).join(", ");
      return { icon: "👀", text: `Found ${jobs.length} reviewed job${jobs.length !== 1 ? "s" : ""}: ${list}.` };
    }

    case "request_apply_approval": {
      const count = (r.job_ids || []).length;
      return { icon: "⏸", text: `Requesting approval to apply to ${count} job${count !== 1 ? "s" : ""}. Waiting for you to confirm.` };
    }

    case "apply_jobs": {
      const { total_applied = 0, total_dry_run = 0, total_failed = 0, total_attempted = 0, errors = [] } = r;
      if (total_dry_run > 0)
        return { icon: "🧪", text: `Dry run: filled ${total_dry_run} form${total_dry_run !== 1 ? "s" : ""} (not submitted).` };
      const parts = [];
      if (total_applied > 0) parts.push(`${total_applied} submitted`);
      if (total_failed > 0) parts.push(`${total_failed} failed`);
      const summary = `Applied to ${total_attempted} job${total_attempted !== 1 ? "s" : ""} — ${parts.join(", ") || "none"}.`;
      const errorDetail = errors.length > 0 ? ` Error: ${errors[0].split(":").slice(-1)[0].trim()}` : "";
      return { icon: "📤", text: summary + errorDetail, isError: total_applied === 0 && total_failed > 0 };
    }

    default:
      return { icon: "·", text: step.tool };
  }
}

// ── Step entry in the reasoning log ───────────────────────────────────────────
function StepRow({ step }) {
  const { icon, text, isError } = formatStep(step);
  return (
    <div className="flex gap-3 py-2.5 border-b border-white/[0.04] last:border-0 items-start">
      <span className="shrink-0 text-sm leading-5">{icon}</span>
      <span className={`text-sm leading-5 ${isError ? "text-red-400" : "text-zinc-300"}`}>
        {text}
      </span>
    </div>
  );
}

// ── Job card in the approval panel ────────────────────────────────────────────
function PendingJobCard({ job, onDismiss }) {
  return (
    <div className="flex items-center justify-between gap-3 px-4 py-3 rounded-lg bg-white/[0.04] border border-white/[0.06]">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className="text-sm text-white font-medium truncate">{job.title}</p>
          {job.url && (
            <a
              href={job.url}
              target="_blank"
              rel="noreferrer"
              className="shrink-0 text-xs text-zinc-500 hover:text-zinc-200 transition-colors underline underline-offset-2"
            >
              View job ↗
            </a>
          )}
        </div>
        <p className="text-xs text-zinc-500 mt-0.5">{job.company}</p>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <ScoreBadge score={job.score} />
        <button
          onClick={() => onDismiss(job.id)}
          title="Remove from apply list"
          className="w-6 h-6 flex items-center justify-center rounded text-zinc-600 hover:text-red-400 hover:bg-red-400/10 transition-colors"
        >
          ✕
        </button>
      </div>
    </div>
  );
}

// ── Main page ──────────────────────────────────────────────────────────────────
// sessionId, sessionData, dryRun and their setters come from App.jsx so they
// survive navigation. loading, error, and goal are local — fine to reset on remount.
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
  const pollRef = useRef(null);   // holds the interval ID so we can clear it

  // ── Derived status ─────────────────────────────────────────────────────────
  const status = sessionData?.status ?? "idle";
  const isRunning = status === "running";
  const isWaiting = status === "waiting_for_approval";
  const isDone = status === "complete" || status === "failed";

  // ── Polling ────────────────────────────────────────────────────────────────
  // Start polling when we have a session and it's in a transient state.
  // Stop when the session reaches a terminal state (complete/failed/waiting).
  //
  // CONCEPT — useEffect with cleanup:
  //   The function returned from useEffect runs when the component unmounts
  //   OR when the dependency array changes. We use it to clear the interval
  //   so we don't keep polling after the session is done or after navigation.
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
        // Don't crash on a transient network error — just wait for next poll
        console.warn("Poll error:", err);
      }
    };

    // Poll immediately, then every 5s.
    // 5s is responsive enough for a process that takes 30-120s per step,
    // and reduces server log noise by ~60% vs the original 2s interval.
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
      // Seed local state so the UI transitions immediately to "running"
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

    // Only approve the jobs that weren't dismissed
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
    setSessionId(null);         // lifted — resets in App
    setSessionData(null);       // lifted — resets in App
    setDryRun(false);           // lifted — resets in App
    setMode("fresh_scan");      // lifted — resets in App
    setHandoff(false);          // lifted — resets in App
    setMaxApply(5);             // lifted — resets in App
    setGoal("");
    setError(null);
    setDismissedIds(new Set());
  };

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div className="p-8 max-w-3xl">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-white text-xl font-semibold mb-1">Orchestrator</h1>
        <p className="text-zinc-500 text-sm">
          AI agent that scrapes, scores, and prepares applications — pausing for your approval before submitting.
        </p>
      </div>

      {/* ── State 1: Idle — goal input ─────────────────────────────────────── */}
      {!sessionId && (
        <div className="space-y-5">

          {/* Mode selector */}
          <div>
            <label className="block text-xs text-zinc-500 mb-2 font-medium uppercase tracking-wider">
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
                  className={`text-left px-4 py-3 rounded-lg border transition-colors ${
                    mode === value
                      ? "border-white/30 bg-white/[0.07] text-white"
                      : "border-white/[0.06] bg-white/[0.02] text-zinc-400 hover:border-white/15 hover:text-zinc-300"
                  }`}
                >
                  <p className="text-sm font-medium">{label}</p>
                  <p className="text-xs text-zinc-500 mt-0.5 leading-relaxed">{description}</p>
                </button>
              ))}
            </div>
          </div>

          {/* Max apply limit */}
          {/* NOTE: This cap controls how many jobs the agent will auto-approve and send
              to the approval panel. Keeping it low (default 5) prevents accidentally
              submitting a huge batch during testing. Range is 1–10. */}
          <div>
            <label className="block text-xs text-zinc-500 mb-2 font-medium uppercase tracking-wider">
              Max applications
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
                className="w-20 bg-white/[0.04] border border-white/[0.08] rounded-lg px-3 py-2 text-sm text-white
                  focus:outline-none focus:border-white/20 transition-colors text-center"
              />
              <span className="text-xs text-zinc-600">
                Agent will prepare at most this many applications per session (1–10)
              </span>
            </div>
          </div>

          {/* Goal input */}
          <div>
            <label className="block text-xs text-zinc-500 mb-2 font-medium uppercase tracking-wider">
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
              className="w-full bg-white/[0.04] border border-white/[0.08] rounded-lg px-4 py-3 text-sm text-white placeholder-zinc-600 resize-none focus:outline-none focus:border-white/20 transition-colors"
            />
          </div>

          {/* Toggles */}
          <div className="space-y-3">
            <label className="flex items-center gap-3 cursor-pointer select-none group">
              <div
                onClick={() => setHandoff((v) => !v)}
                className={`relative w-9 h-5 rounded-full transition-colors ${
                  handoff ? "bg-blue-600" : "bg-zinc-700"
                }`}
              >
                <div
                  className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${
                    handoff ? "translate-x-4" : "translate-x-0.5"
                  }`}
                />
              </div>
              <span className="text-sm text-zinc-400 group-hover:text-zinc-300 transition-colors">
                Handoff mode{" "}
                <span className="text-zinc-600 text-xs">
                  (fill forms in a visible browser, you submit)
                </span>
              </span>
            </label>

            <label className="flex items-center gap-3 cursor-pointer select-none group">
              <div
                onClick={() => setDryRun((v) => !v)}
                className={`relative w-9 h-5 rounded-full transition-colors ${
                  dryRun ? "bg-blue-600" : "bg-zinc-700"
                }`}
              >
                <div
                  className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${
                    dryRun ? "translate-x-4" : "translate-x-0.5"
                  }`}
                />
              </div>
              <span className="text-sm text-zinc-400 group-hover:text-zinc-300 transition-colors">
                Dry run{" "}
                <span className="text-zinc-600 text-xs">
                  (fill forms + screenshot, never submit)
                </span>
              </span>
            </label>
          </div>

          {error && (
            <p className="text-red-400 text-sm">{error}</p>
          )}

          <button
            onClick={handleStart}
            disabled={loading || !goal.trim()}
            className="px-5 py-2.5 bg-white text-black text-sm font-semibold rounded-lg
              hover:bg-zinc-200 active:bg-zinc-300 transition-colors
              disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {loading ? "Starting…" : "Start Agent"}
          </button>

          <p className="text-zinc-600 text-xs">⌘↵ to start</p>
        </div>
      )}

      {/* ── State 2: Running — reasoning log ──────────────────────────────── */}
      {sessionId && (isRunning) && (
        <div className="space-y-5">
          {/* Pulsing status indicator — shows what's happening right now */}
          <div className="flex items-center gap-3">
            <div className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse shrink-0" />
            <span className="text-sm text-zinc-300">
              {(() => {
                const steps = sessionData?.steps ?? [];
                const lastStep = steps[steps.length - 1];
                // If the last step was the approval gate, we're now in the apply phase
                if (lastStep?.tool === "request_apply_approval") return "Submitting applications…";
                if (lastStep) return formatStep(lastStep).text;
                return "Starting…";
              })()}
              {" "}
              <span className="text-zinc-600 text-xs">
                ({sessionData?.token_usage ?? 0} tokens)
              </span>
            </span>
          </div>

          {/* Reasoning log */}
          <div className="bg-white/[0.03] border border-white/[0.06] rounded-lg p-4 max-h-96 overflow-y-auto">
            <p className="text-xs text-zinc-600 mb-3 font-medium uppercase tracking-wider">
              Reasoning log
            </p>
            {sessionData?.steps?.length === 0 ? (
              <p className="text-xs text-zinc-600">Waiting for first tool call…</p>
            ) : (
              sessionData?.steps?.map((step, i) => (
                <StepRow key={i} step={step} />
              ))
            )}
          </div>

          <button
            onClick={handleCancel}
            className="text-xs text-zinc-600 hover:text-zinc-400 transition-colors"
          >
            Cancel session
          </button>
        </div>
      )}

      {/* ── State 3: Waiting for approval ─────────────────────────────────── */}
      {sessionId && isWaiting && (
        <div className="space-y-5">
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full bg-yellow-500" />
            <span className="text-sm text-yellow-400 font-medium">
              Paused — review these jobs before applying
            </span>
          </div>

          {/* Reasoning log (collapsed) */}
          {sessionData?.steps?.length > 0 && (
            <details className="group">
              <summary className="text-xs text-zinc-600 cursor-pointer hover:text-zinc-400 transition-colors list-none">
                Show reasoning log ({sessionData.steps.length} steps) ▸
              </summary>
              <div className="mt-2 bg-white/[0.03] border border-white/[0.06] rounded-lg p-4 max-h-48 overflow-y-auto">
                {sessionData.steps.map((step, i) => (
                  <StepRow key={i} step={step} />
                ))}
              </div>
            </details>
          )}

          {/* Pending job cards — user can dismiss any they don't want to apply to */}
          {(() => {
            const allJobs = sessionData?.pending_jobs ?? [];
            const visibleJobs = allJobs.filter((j) => !dismissedIds.has(j.id));
            const dismissedCount = allJobs.length - visibleJobs.length;
            return (
              <>
                <div className="space-y-2">
                  {allJobs.length === 0 ? (
                    <p className="text-sm text-zinc-500">No jobs pending approval.</p>
                  ) : (
                    visibleJobs.map((job) => (
                      <PendingJobCard key={job.id} job={job} onDismiss={handleDismiss} />
                    ))
                  )}
                  {dismissedCount > 0 && (
                    <p className="text-xs text-zinc-600 pt-1">
                      {dismissedCount} job{dismissedCount !== 1 ? "s" : ""} removed —{" "}
                      <button
                        onClick={() => setDismissedIds(new Set())}
                        className="underline hover:text-zinc-400 transition-colors"
                      >
                        undo
                      </button>
                    </p>
                  )}
                </div>

                {error && <p className="text-red-400 text-sm">{error}</p>}

                <div className="flex gap-3">
                  <button
                    onClick={handleApprove}
                    disabled={visibleJobs.length === 0}
                    className="px-5 py-2.5 bg-emerald-600 text-white text-sm font-semibold rounded-lg
                      hover:bg-emerald-500 active:bg-emerald-700 transition-colors
                      disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    Approve & Apply ({visibleJobs.length} job{visibleJobs.length !== 1 ? "s" : ""})
                  </button>
                  <button
                    onClick={handleCancel}
                    className="px-5 py-2.5 bg-white/[0.06] text-zinc-400 text-sm font-semibold rounded-lg
                      hover:bg-white/[0.10] transition-colors"
                  >
                    Cancel
                  </button>
                </div>

                <p className="text-xs text-zinc-600">
                  {dryRun && "Dry run mode — forms will be filled and screenshotted but not submitted."}
                </p>
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
              className={`w-2 h-2 rounded-full ${
                status === "complete" ? "bg-emerald-500" : "bg-red-500"
              }`}
            />
            <span className={`text-sm font-medium ${
              status === "complete" ? "text-emerald-400" : "text-red-400"
            }`}>
              {status === "complete" ? "Session complete" : "Session failed"}
            </span>
          </div>

          {/* Result summary */}
          {sessionData?.result_summary && (
            <div className="bg-white/[0.03] border border-white/[0.06] rounded-lg p-4">
              <p className="text-xs text-zinc-600 mb-2 font-medium uppercase tracking-wider">
                Summary
              </p>
              <p className="text-sm text-zinc-300 leading-relaxed">
                {sessionData.result_summary}
              </p>
            </div>
          )}

          {/* Token stats */}
          <div className="flex gap-4 text-xs text-zinc-600">
            <span>
              Tokens used:{" "}
              <span className="text-zinc-400">{sessionData?.token_usage ?? 0}</span>
            </span>
            <span>
              Steps:{" "}
              <span className="text-zinc-400">{sessionData?.steps?.length ?? 0}</span>
            </span>
          </div>

          {/* Full reasoning log */}
          {sessionData?.steps?.length > 0 && (
            <details>
              <summary className="text-xs text-zinc-600 cursor-pointer hover:text-zinc-400 transition-colors list-none">
                Show full reasoning log ▸
              </summary>
              <div className="mt-2 bg-white/[0.03] border border-white/[0.06] rounded-lg p-4 max-h-64 overflow-y-auto">
                {sessionData.steps.map((step, i) => (
                  <StepRow key={i} step={step} />
                ))}
              </div>
            </details>
          )}

          <button
            onClick={handleNewSession}
            className="px-5 py-2.5 bg-white text-black text-sm font-semibold rounded-lg
              hover:bg-zinc-200 active:bg-zinc-300 transition-colors"
          >
            Start New Session
          </button>
        </div>
      )}
    </div>
  );
}
