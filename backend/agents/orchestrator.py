"""
agents/orchestrator.py — The Orchestrator agent loop.

WHAT THIS FILE DOES:
  This is the "Imperative Shell" — all side effects live here:
    - Claude API calls (Anthropic tool-use)
    - DB reads/writes (OrchestratorSession, Job)
    - Spawning real agents (scraper, resume_match)
    - Token tracking

  The pure logic (prompt building, response parsing, tool definitions) lives
  in orchestrator_logic.py and has zero side effects.

ARCHITECTURE — The two-phase loop:
  Phase A (run):    The agent loop runs until it hits `request_apply_approval`
                    or `end_turn`. On approval gate: save pending job IDs,
                    set status=waiting_for_approval, exit. On end_turn: complete.

  Phase B (resume): Called after the human approves. Runs the Apply Agent with
                    the approved job IDs and finalizes the session.

  WHY SPLIT INTO TWO PHASES?
    The approval gate could sit open for minutes (while the user reviews) or days.
    If we kept the Claude conversation "open" in memory for that whole time, it would
    disappear on server restart. By persisting the pending state to the DB, we can
    reconstruct the session cleanly when resume() is called — no memory required.

CONCEPT — Anthropic tool-use loop:
  1. Build messages list (starts with the user's goal as the first human message)
  2. Call Claude API with tools list
  3. If Claude returns tool_use blocks → execute the tools → append results → go to 2
  4. If Claude returns only text (no tool_use) → session is complete
  5. Track tokens after every Claude call

EXCEPTION DESIGN — ApprovalGateTriggered:
  When the agent calls `request_apply_approval`, we raise this exception to
  break out of the loop cleanly. It carries the job IDs so the caller can
  save them. Using an exception (instead of a return value) avoids threading
  the approval flag through every level of the loop.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone

import anthropic
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agents.orchestrator_logic import (
    OrchestratorConfig,
    OrchestratorResult,
    build_system_prompt,
    build_tool_definitions,
    build_tool_result_message,
    parse_tool_calls,
)
from core.config import settings
from core.database import get_db_context
from models.job import Job, JobStatus
from models.orchestrator_session import OrchestratorSession, SessionStatus

logger = logging.getLogger(__name__)


# ── Custom Exceptions ──────────────────────────────────────────────────────────

class ApprovalGateTriggered(Exception):
    """
    Raised when the agent calls `request_apply_approval`.

    Carrying the job_ids and reasoning as attributes means the loop-exiting
    logic in _run_loop() can persist them to the DB without needing a return value.

    WHY AN EXCEPTION AND NOT A RETURN FLAG?
      The tool execution is several function calls deep. To propagate "stop the loop"
      back up to _run_loop() via a return value, every intermediate function would
      need to check and re-return a special sentinel. An exception short-circuits
      all of that and lands directly in the except block at the top of _run_loop().
    """
    def __init__(self, job_ids: list[str], reasoning: str) -> None:
        self.job_ids = job_ids
        self.reasoning = reasoning
        super().__init__(f"Approval gate triggered for {len(job_ids)} job(s)")


# ── Public Entry Points ────────────────────────────────────────────────────────

async def run(
    goal: str,
    mode: str = "fresh_scan",
    max_apply: int = 5,
    db_session: AsyncSession | None = None,
) -> OrchestratorResult:
    """
    Start a new Orchestrator session for the given goal.

    Creates an OrchestratorSession in the DB, then runs the agent loop.
    The loop calls Claude API with tool-use until either:
      - Claude decides it's done (no more tool calls) → status=complete
      - Claude calls request_apply_approval → status=waiting_for_approval
      - max_turns is exceeded → status=failed

    Args:
        goal:       Natural-language goal for the session (e.g. "Find me 5 good jobs").
        db_session: Optional AsyncSession. If None, opens its own session.
                    Pass a session in tests to use the test DB.

    Returns:
        OrchestratorResult with the final session state.
    """
    config = OrchestratorConfig(
        model=settings.orchestrator_model,
        max_turns=settings.orchestrator_max_turns,
        max_tokens=settings.orchestrator_max_tokens,
        dry_run=settings.orchestrator_dry_run,
        mode=mode,
        max_apply=max_apply,
    )

    async def _run(session: AsyncSession) -> OrchestratorResult:
        # Create the session record in the DB immediately.
        # This gives us a session_id to return to the API caller right away,
        # even before any agent work starts.
        db_record = OrchestratorSession(goal=goal)
        session.add(db_record)
        await session.flush()  # flush to get the ID without committing

        result = OrchestratorResult(session_id=db_record.id)

        try:
            await _run_loop(goal, config, result, db_record, session)
        except Exception as exc:
            result.status = "failed"
            result.errors.append(str(exc))
            logger.error(
                "Orchestrator session failed",
                extra={"session_id": str(db_record.id), "error": str(exc)},
            )

        # Persist final state to DB
        db_record.status = SessionStatus(result.status)
        db_record.steps = result.steps
        db_record.token_usage = result.token_usage
        db_record.result_summary = result.result_summary
        await session.commit()

        return result

    if db_session is not None:
        return await _run(db_session)
    else:
        async with get_db_context() as session:
            return await _run(session)


async def resume(
    session_id: uuid.UUID,
    approved_job_ids: list[str],
    dry_run: bool = False,
    handoff: bool = False,
    db_session: AsyncSession | None = None,
) -> OrchestratorResult:
    """
    Resume a session that is waiting_for_approval by running the Apply Agent.

    Called after the human approves a job list via POST /api/v1/orchestrator/approve/{id}.

    Args:
        session_id:        UUID of the OrchestratorSession to resume.
        approved_job_ids:  Job ID strings the user approved. In dry_run mode these
                           may be mock IDs (e.g. "mock-uuid-1"), not real UUIDs.
                           UUID conversion happens inside _execute_apply, only for
                           the real (non-dry-run) apply path.
        db_session:        Optional AsyncSession (for testing).

    Returns:
        OrchestratorResult with the final session state after applying.
    """
    async def _resume(session: AsyncSession) -> OrchestratorResult:
        # Load the existing session record
        db_record = await session.get(OrchestratorSession, session_id)
        if not db_record:
            raise ValueError(f"Session {session_id} not found")
        if db_record.status != SessionStatus.WAITING_FOR_APPROVAL:
            raise ValueError(f"Session {session_id} is not waiting_for_approval (got {db_record.status})")

        result = OrchestratorResult(
            session_id=db_record.id,
            status="running",
            steps=list(db_record.steps),
            token_usage=db_record.token_usage,
        )

        # Run the Apply Agent with the approved job IDs
        apply_step = {
            "tool": "apply_jobs",
            "input": {"job_ids": [str(jid) for jid in approved_job_ids]},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        try:
            apply_result_data = await _execute_apply(
                approved_job_ids,
                config=OrchestratorConfig(
                    model=settings.orchestrator_model,
                    max_turns=settings.orchestrator_max_turns,
                    max_tokens=settings.orchestrator_max_tokens,
                    # Use the dry_run flag from the original run() call, not settings —
                    # settings may have already been reset to its default by this point.
                    dry_run=dry_run,
                ),
                handoff=handoff,
            )
            apply_step["result"] = apply_result_data
            result.status = "complete"

            # Build a plain-English summary including any per-job errors
            n_applied = apply_result_data.get("total_applied", 0)
            n_failed = apply_result_data.get("total_failed", 0)
            n_dry = apply_result_data.get("total_dry_run", 0)
            errors = apply_result_data.get("errors", [])
            summary = (
                f"Session complete. "
                f"Applied to {n_applied} job(s). "
                f"Dry-run: {n_dry}. "
                f"Failed: {n_failed}."
            )
            if errors:
                summary += " Errors: " + " | ".join(errors)
            result.result_summary = summary
        except Exception as exc:
            apply_step["error"] = str(exc)
            result.status = "failed"
            result.errors.append(str(exc))
            result.result_summary = f"Apply phase failed: {exc}"

        result.steps.append(apply_step)

        # Persist to DB
        db_record.status = SessionStatus(result.status)
        db_record.steps = result.steps
        db_record.token_usage = result.token_usage
        db_record.result_summary = result.result_summary
        db_record.pending_job_ids = None  # cleared on approval
        await session.commit()

        return result

    if db_session is not None:
        return await _resume(db_session)
    else:
        async with get_db_context() as session:
            return await _resume(session)


# ── Inner Loop ─────────────────────────────────────────────────────────────────

async def _run_loop(
    goal: str,
    config: OrchestratorConfig,
    result: OrchestratorResult,
    db_record: OrchestratorSession,
    session: AsyncSession,
) -> None:
    """
    The core agent loop: call Claude → execute tools → repeat.

    Exits when:
      - Claude returns a response with no tool calls (it's done)
      - ApprovalGateTriggered is raised (pause for human approval)
      - turn_count >= config.max_turns (safety cap)

    CONCEPT — The messages list:
      Anthropic's API is "stateless" — it doesn't remember previous calls.
      We must send the ENTIRE conversation history on every API call.
      The messages list grows with each turn:
        Turn 1: [{"role": "user", "content": goal_message}]
        Turn 2: [previous..., {"role": "assistant", "content": claude_turn1}, tool_result]
        Turn 3: [previous..., {"role": "assistant", "content": claude_turn2}, tool_result]
      This is memory-intensive for long sessions but correct and standard.
    """
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    tools = build_tool_definitions()

    # Get initial DB state for the system prompt
    initial_db_state = await _get_db_state(session)
    system_prompt = build_system_prompt(goal, initial_db_state, mode=config.mode, max_apply=config.max_apply)

    # Start the conversation with the user's goal as the first message
    messages: list[dict] = [
        {"role": "user", "content": goal}
    ]

    for turn in range(config.max_turns):
        logger.info(
            "Orchestrator turn %d/%d",
            turn + 1,
            config.max_turns,
            extra={"session_id": str(result.session_id)},
        )

        # ── Call Claude ───────────────────────────────────────────────────
        # NOTE: The Anthropic SDK is synchronous (it uses httpx under the hood
        # in a blocking way). We wrap it in asyncio.to_thread() so we don't
        # block the entire event loop while waiting for Claude's response.
        # This is the same pattern used in resume_match.py for score_job().
        response = await asyncio.to_thread(
            client.messages.create,
            model=config.model,
            max_tokens=config.max_tokens,
            system=system_prompt,
            tools=tools,
            messages=messages,
        )

        # Track token usage after every call
        result.token_usage += response.usage.input_tokens + response.usage.output_tokens
        db_record.token_usage = result.token_usage

        # Append Claude's response to the conversation history
        messages.append({"role": "assistant", "content": response.content})

        # ── Extract tool calls ─────────────────────────────────────────────
        tool_calls = parse_tool_calls(response.content)

        # If no tool calls: Claude is done — extract the final text and exit
        if not tool_calls:
            # Find the last text block in Claude's response for the summary
            for block in response.content:
                if hasattr(block, "type") and block.type == "text":
                    result.result_summary = block.text
            result.status = "complete"
            logger.info(
                "Orchestrator session complete",
                extra={"session_id": str(result.session_id), "turns": turn + 1},
            )
            return

        # ── Execute each tool call ─────────────────────────────────────────
        # NOTE: Claude can call multiple tools in one response (parallel calling).
        # We execute them sequentially here (simpler), but could parallelize later.
        tool_result_messages = []
        for tool_use_id, tool_name, tool_input in tool_calls:
            step = {
                "tool": tool_name,
                "input": tool_input,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            try:
                # ApprovalGateTriggered will propagate up through here if raised
                tool_result = await _execute_tool(tool_name, tool_input, config, session)
                step["result"] = tool_result
                logger.info(
                    "Tool executed: %s",
                    tool_name,
                    extra={"session_id": str(result.session_id), "result": tool_result},
                )
            except ApprovalGateTriggered as gate:
                # ── Approval gate ──────────────────────────────────────────
                # Save the pending job IDs and suspend the session.
                # The API will expose them for the frontend to display.
                step["result"] = {
                    "status": "waiting_for_approval",
                    "job_ids": gate.job_ids,
                    "reasoning": gate.reasoning,
                }
                result.steps.append(step)
                result.status = "waiting_for_approval"

                # Build pending job details by scanning back through steps for the
                # most recent get_reviewed_jobs result. We store full dicts
                # (id, title, company, score, url) so the status endpoint can display
                # job cards WITHOUT doing a DB lookup — which would fail for mock IDs
                # in dry_run mode, and avoids an extra query in real mode.
                pending_job_details = _extract_pending_job_details(result.steps, gate.job_ids)

                # Persist pending state immediately
                db_record.status = SessionStatus.WAITING_FOR_APPROVAL
                db_record.pending_job_ids = pending_job_details  # list of dicts, not just IDs
                db_record.steps = result.steps
                await session.commit()

                logger.info(
                    "Approval gate triggered",
                    extra={
                        "session_id": str(result.session_id),
                        "job_count": len(gate.job_ids),
                    },
                )
                return  # Exit the loop — session is now suspended

            except Exception as exc:
                # ── Per-tool error handling ────────────────────────────────
                # Instead of crashing the whole session when one tool fails
                # (e.g. resume not found, scraper network error), we report the
                # error back to Claude as a tool result. Claude can then decide
                # to stop gracefully with a clear summary rather than the session
                # dying with a cryptic traceback.
                #
                # WHY NOT LET IT PROPAGATE:
                #   Unhandled exceptions here would be caught by run()'s outer
                #   try/except and set status="failed" with no reasoning log entry,
                #   leaving the frontend stuck on an opaque "Session failed" state.
                error_msg = str(exc)
                step["error"] = error_msg
                tool_result = {
                    "error": error_msg,
                    "success": False,
                    "tool": tool_name,
                }
                logger.error(
                    "Tool raised an exception: %s — %s",
                    tool_name,
                    error_msg,
                    extra={"session_id": str(result.session_id)},
                    exc_info=True,
                )

            result.steps.append(step)
            tool_result_messages.append(build_tool_result_message(tool_use_id, tool_result))

        # ── Feed results back to Claude ────────────────────────────────────
        # Claude needs ALL tool results before it can make its next decision.
        # If Claude called 3 tools, we must send 3 tool_result messages.
        # NOTE: The Anthropic API requires all tool results from one turn to
        # be batched into a single user message.
        if len(tool_result_messages) == 1:
            messages.append(tool_result_messages[0])
        elif len(tool_result_messages) > 1:
            # Merge multiple tool results into one user message with multiple content blocks
            combined_content = []
            for msg in tool_result_messages:
                combined_content.extend(msg["content"])
            messages.append({"role": "user", "content": combined_content})

        # Flush intermediate state to DB after each turn so polling is live
        db_record.steps = result.steps
        db_record.token_usage = result.token_usage
        await session.commit()

    # ── Max turns exceeded ─────────────────────────────────────────────────
    result.status = "failed"
    result.result_summary = f"Session exceeded maximum turns ({config.max_turns}). Stopping."
    result.errors.append(f"Max turns ({config.max_turns}) exceeded")
    logger.warning(
        "Orchestrator exceeded max_turns",
        extra={"session_id": str(result.session_id), "max_turns": config.max_turns},
    )


# ── Tool Dispatch ──────────────────────────────────────────────────────────────

async def _execute_tool(
    tool_name: str,
    tool_input: dict,
    config: OrchestratorConfig,
    session: AsyncSession,
) -> dict:
    """
    Dispatch a tool call to the appropriate agent or DB query.

    DESIGN — dry_run mode:
      When dry_run=True, all tools return plausible mock data instead of
      running real agents. This lets you test the full orchestrator loop
      (prompt → tool calls → reasoning → approval gate) without:
        - Making real HTTP requests to job boards
        - Spending Claude API credits for scoring
        - Opening Playwright browsers

    Args:
        tool_name:  The name from Claude's tool_use block.
        tool_input: The arguments dict from Claude's tool_use block.
        config:     OrchestratorConfig (dry_run flag lives here).
        session:    AsyncSession for DB queries.

    Returns:
        Dict with the tool's result (serializable to JSON for Claude).

    Raises:
        ApprovalGateTriggered: if tool_name == "request_apply_approval"
        ValueError: if tool_name is not recognized
    """
    if tool_name == "check_db_state":
        if config.dry_run:
            return {"total": 15, "new": 3, "scored": 8, "reviewed": 4, "ignored": 0, "applied": 0}
        return await _get_db_state(session)

    elif tool_name == "scrape_jobs":
        if config.dry_run:
            return {"new_jobs": 5, "duplicates": 2, "errors": 0, "dry_run": True}
        # Import here (not at top) to keep the dependency lazy —
        # orchestrator_logic.py has zero imports from other agents.
        from agents.scraper import run as scraper_run
        scrape_result = await scraper_run()
        return {
            "new_jobs": scrape_result.total_new,
            "duplicates": scrape_result.total_duplicate,
            "errors": scrape_result.total_errors,
        }

    elif tool_name == "score_jobs":
        if config.dry_run:
            return {"scored": 5, "failed": 0, "skipped": 0, "dry_run": True}
        from agents.resume_match import run as score_run
        score_result = await score_run()
        return {
            "scored": score_result.total_scored,
            "failed": score_result.total_errors,
            "skipped": score_result.total_skipped,
        }

    elif tool_name == "auto_review_jobs":
        min_score = float(tool_input.get("min_score", 70.0))
        limit = int(tool_input.get("limit", config.max_apply))
        if config.dry_run:
            return {"reviewed": min(4, limit), "min_score": min_score, "limit": limit, "dry_run": True}
        return await _auto_review_jobs(session, min_score, limit)

    elif tool_name == "get_reviewed_jobs":
        min_score = float(tool_input.get("min_score", 70.0))
        if config.dry_run:
            return {
                "jobs": [
                    {"id": "mock-uuid-1", "title": "Software Engineer", "company": "Acme", "score": 85.0},
                    {"id": "mock-uuid-2", "title": "Backend Engineer", "company": "Globex", "score": 78.0},
                ],
                "count": 2,
                "dry_run": True,
            }
        return await _get_reviewed_jobs(session, min_score)

    elif tool_name == "request_apply_approval":
        job_ids = tool_input.get("job_ids", [])
        reasoning = tool_input.get("reasoning", "")
        # Raise the exception to cleanly exit the loop and suspend the session.
        raise ApprovalGateTriggered(job_ids=job_ids, reasoning=reasoning)

    else:
        raise ValueError(f"Unknown tool: {tool_name}")


async def _execute_apply(
    approved_job_ids: list[str],
    config: OrchestratorConfig,
    handoff: bool = False,
) -> dict:
    """
    Run the Apply Agent with the approved job IDs.

    Called only from resume(), after human approval.
    Dry-run mode returns mock counts without converting IDs — safe for mock UUIDs.
    Real mode converts strings to uuid.UUID here (the only place it's needed).

    Args:
        approved_job_ids: Job ID strings to apply to.
        config:           OrchestratorConfig (dry_run flag, etc.).
        handoff:          If True, fill forms in a visible browser and pause for the
                          user to complete remaining fields before submitting.
    """
    if config.dry_run:
        return {
            "total_attempted": len(approved_job_ids),
            "total_applied": 0,
            "total_dry_run": len(approved_job_ids),
            "total_failed": 0,
            "dry_run": True,
        }

    # Convert to real UUIDs only in the real-apply path — mock IDs never reach here
    job_uuids = [uuid.UUID(jid) for jid in approved_job_ids]
    from agents.apply import run as apply_run
    apply_result = await apply_run(
        job_ids=job_uuids,
        dry_run=False,
        handoff=handoff,
    )
    return {
        "total_attempted": apply_result.total_attempted,
        "total_applied": apply_result.total_applied,
        "total_dry_run": apply_result.total_dry_run,
        "total_failed": apply_result.total_failed,
        "errors": apply_result.errors,
    }


# ── DB Helpers ─────────────────────────────────────────────────────────────────

async def _get_db_state(session: AsyncSession) -> dict:
    """
    Return a snapshot of current job counts by status.

    QUERY STRATEGY — GROUP BY status:
      One SQL query with GROUP BY is much more efficient than running 5 separate
      COUNT queries (one per status). The result is a list of (status, count) rows
      which we convert to a dict keyed by status name.

    NOTE: We filter deleted_at IS NULL to exclude soft-deleted jobs.
      The "Clear All Jobs" button in the UI soft-deletes jobs (sets deleted_at).
      Without this filter, _get_db_state would count deleted jobs as "new",
      causing the agent to think there are unscored jobs when resume_match
      (which also filters deleted_at) would find nothing to score — leading
      to confusing "no jobs found" results even when the DB looks populated.
    """
    rows = await session.execute(
        select(Job.status, func.count(Job.id).label("count"))
        .where(Job.deleted_at.is_(None))
        .group_by(Job.status)
    )
    counts: dict[str, int] = {row.status.value: row.count for row in rows}

    total = sum(counts.values())
    return {
        "total": total,
        "new": counts.get("new", 0),
        "scored": counts.get("scored", 0),
        "reviewed": counts.get("reviewed", 0),
        "ignored": counts.get("ignored", 0),
        "applied": counts.get("applied", 0),
    }


async def _get_reviewed_jobs(session: AsyncSession, min_score: float) -> dict:
    """
    Fetch REVIEWED jobs above the score threshold.

    Returns a dict with a `jobs` list (each job has id, title, company, score, url)
    and a `count` integer. Claude uses this to decide which jobs to request approval for.
    """
    rows = await session.execute(
        select(Job)
        .where(Job.status == JobStatus.REVIEWED)
        .where(Job.match_score >= min_score)
        .where(Job.deleted_at.is_(None))
        .order_by(Job.match_score.desc())
    )
    jobs = rows.scalars().all()

    return {
        "jobs": [
            {
                "id": str(job.id),
                "title": job.title,
                "company": job.company,
                "score": job.match_score,
                "url": job.source_url,
            }
            for job in jobs
        ],
        "count": len(jobs),
    }


async def _auto_review_jobs(session: AsyncSession, min_score: float, limit: int = 5) -> dict:
    """
    Bulk-mark all SCORED jobs above min_score as REVIEWED.

    WHY THIS EXISTS:
      After scraping + scoring, jobs sit in "scored" status. To make them eligible
      for the apply approval gate, a human normally reviews them in the Jobs UI.
      In fresh_scan mode we skip that manual step — the orchestrator promotes all
      high-scoring jobs to "reviewed" automatically so the pipeline can proceed.

      This is intentionally separate from `get_reviewed_jobs` so the system prompt
      can guide Claude to call it only in fresh_scan mode, preserving manual review
      as the default for use_reviewed sessions.

    Args:
        session:   Active DB session.
        min_score: Jobs with match_score >= this become "reviewed".

    Returns:
        Dict with count of jobs promoted and the threshold used.
    """
    # Select the top `limit` scored jobs above the threshold (best score first)
    # then mark only those as reviewed. Using a subquery so we can ORDER + LIMIT
    # before doing the UPDATE — plain UPDATE doesn't support ORDER BY in PostgreSQL.
    subq = (
        select(Job.id)
        .where(Job.status == JobStatus.SCORED)
        .where(Job.match_score >= min_score)
        .where(Job.deleted_at.is_(None))
        .order_by(Job.match_score.desc())
        .limit(limit)
        .scalar_subquery()
    )
    result = await session.execute(
        update(Job)
        .where(Job.id.in_(subq))
        .values(status=JobStatus.REVIEWED)
        .returning(Job.id)
    )
    reviewed_ids = result.fetchall()
    await session.flush()

    return {
        "reviewed": len(reviewed_ids),
        "min_score": min_score,
        "limit": limit,
    }


def _extract_pending_job_details(steps: list[dict], approved_job_ids: list[str]) -> list[dict]:
    """
    Build a list of job detail dicts for the approval gate display.

    WHY THIS EXISTS:
      When Claude calls `request_apply_approval`, it passes the job IDs it chose.
      We need to show the user job cards (title, company, score) in the UI —
      but we can't do a DB lookup at this point (orchestrator.py doesn't own the
      HTTP request/response cycle, and in dry_run mode the IDs aren't real UUIDs).

      Instead, we scan backwards through the reasoning log for the most recent
      `get_reviewed_jobs` step and extract the job details from its result.
      This works for both real and dry_run sessions because get_reviewed_jobs
      always returns full job dicts regardless of mode.

    Args:
        steps:            The accumulated reasoning log (list of step dicts).
        approved_job_ids: The job IDs Claude passed to request_apply_approval.

    Returns:
        List of job dicts with id, title, company, score, url fields.
        Falls back to minimal {"id": job_id} dicts if no matching details found.
    """
    # Scan backwards through steps to find the last get_reviewed_jobs result
    reviewed_jobs_by_id: dict[str, dict] = {}
    for step in reversed(steps):
        if step.get("tool") == "get_reviewed_jobs" and step.get("result"):
            for job in step["result"].get("jobs", []):
                reviewed_jobs_by_id[job["id"]] = job
            break  # Only need the most recent get_reviewed_jobs call

    # Map each approved ID to its details (fall back to minimal dict if not found)
    result = []
    for job_id in approved_job_ids:
        if job_id in reviewed_jobs_by_id:
            result.append(reviewed_jobs_by_id[job_id])
        else:
            # Fallback: at minimum include the ID so the frontend can show something
            result.append({"id": job_id, "title": "Unknown", "company": "Unknown", "score": None})
    return result
