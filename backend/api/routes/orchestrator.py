"""
api/routes/orchestrator.py — Endpoints to start, monitor, approve, and review Orchestrator sessions.

ENDPOINTS:
  POST /api/v1/orchestrator/run          — Start a new orchestrator session
  GET  /api/v1/orchestrator/status/{id}  — Poll session progress
  POST /api/v1/orchestrator/approve/{id} — Approve the pending job list (triggers apply)
  GET  /api/v1/orchestrator/history      — List past sessions

ARCHITECTURE — Why BackgroundTasks (not Celery)?
  Same reasoning as pipeline.py: Celery requires a separate worker process.
  BackgroundTasks runs in the same server process — simpler for development,
  correct for single-server production.

  If you scale to multiple servers: replace `background_tasks.add_task()` with
  `orchestrate_task.delay()` and implement a Celery task in workers/tasks.py.
  One-line change.

IN-MEMORY STATE:
  `_sessions` is a module-level dict keyed by session UUID.
  It mirrors the DB — the DB is the source of truth, but we cache the latest
  state here so GET /status/{id} can respond without a DB query when the session
  is actively running in memory.

  On server restart, _sessions is empty. Clients must use GET /history to see
  past sessions and GET /status/{id} to reload from the DB.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from models.orchestrator_session import OrchestratorSession, SessionStatus

router = APIRouter()


# ── In-memory session cache ────────────────────────────────────────────────────
# Key: session UUID (str), Value: dict with current session state
# This is populated when a session starts and updated when it completes.
# Used by GET /status/{id} to return live state without a DB round-trip.
_sessions: dict[str, dict] = {}


# ── Request / Response schemas ─────────────────────────────────────────────────

class RunRequest(BaseModel):
    """
    Request body for POST /run.
    goal is required (what the orchestrator should accomplish).
    dry_run defaults to False — the settings default applies.
    mode: "fresh_scan" runs the full pipeline (scrape → score → auto-review → apply).
          "use_reviewed" only works with jobs already manually reviewed in the UI.
    """
    goal: str
    dry_run: bool = False
    mode: str = "fresh_scan"
    handoff: bool = False
    max_apply: int = 5


class RunResponse(BaseModel):
    session_id: str
    status: str   # always "started"


class ApproveRequest(BaseModel):
    """
    Optional body for POST /approve/{id}.
    If approved_job_ids is omitted, all pending jobs are approved.
    This lets the frontend offer "Approve All" as the default action.
    """
    approved_job_ids: list[str] | None = None


class SessionStep(BaseModel):
    tool: str
    input: dict = {}
    result: dict | None = None
    error: str | None = None
    timestamp: str | None = None


class PendingJob(BaseModel):
    id: str
    title: str
    company: str
    score: float | None = None
    url: str | None = None


class StatusResponse(BaseModel):
    session_id: str
    status: str
    goal: str
    steps: list[dict]
    pending_jobs: list[PendingJob]
    token_usage: int
    result_summary: str | None
    created_at: str | None = None


class HistoryItem(BaseModel):
    session_id: str
    status: str
    goal: str
    token_usage: int
    result_summary: str | None
    created_at: str | None


# ── Background task functions ──────────────────────────────────────────────────

async def _run_orchestrator(session_id: str, goal: str, dry_run: bool, mode: str = "fresh_scan", max_apply: int = 5) -> None:
    """
    Background task: run the orchestrator for an already-created session.

    IMPORTANT: This runs AFTER the HTTP response is sent. orchestrator.run()
    creates its own DB session internally — we do NOT open one here because
    opening a second session for the same work would be wasteful and confusing.

    NOTE: We override the dry_run setting temporarily if the request specified it.
    We do this by patching the settings object locally — not ideal, but avoids
    threading dry_run through every function signature just for this case.
    """
    from agents.orchestrator import run as orchestrator_run
    from core.config import settings

    # Temporarily override dry_run if the request asked for it.
    # Restore it in `finally` so any other parallel sessions aren't affected.
    original_dry_run = settings.orchestrator_dry_run
    settings.orchestrator_dry_run = dry_run

    try:
        result = await orchestrator_run(goal=goal, mode=mode, max_apply=max_apply)
        _sessions[session_id]["status"] = result.status
        _sessions[session_id]["steps"] = result.steps
        _sessions[session_id]["token_usage"] = result.token_usage
        _sessions[session_id]["result_summary"] = result.result_summary
        # orchestrator.run() creates a NEW OrchestratorSession in the DB with its own UUID.
        # Update our in-memory cache to point to that real DB record so that POST /approve
        # can load it by the correct ID.
        _sessions[session_id]["db_session_id"] = str(result.session_id)
    except Exception as exc:
        _sessions[session_id]["status"] = "failed"
        _sessions[session_id]["result_summary"] = str(exc)
    finally:
        settings.orchestrator_dry_run = original_dry_run


async def _resume_orchestrator(
    session_id: str,
    db_session_id: str,
    approved_job_ids: list[str],
    dry_run: bool = False,
    handoff: bool = False,
) -> None:
    """
    Background task: run the Apply Agent after human approval.
    dry_run must be passed explicitly — it can't be read from settings here because
    the original run() background task already reset it back to the default.
    """
    from agents.orchestrator import resume as orchestrator_resume

    try:
        # Normalise: items may be plain ID strings or dicts with an "id" key.
        # Pass as strings — UUID conversion happens inside _execute_apply only
        # for real (non-dry-run) sessions where IDs are genuine DB UUIDs.
        id_strings = [jid["id"] if isinstance(jid, dict) else jid for jid in approved_job_ids]
        result = await orchestrator_resume(
            session_id=uuid.UUID(db_session_id),
            approved_job_ids=id_strings,
            dry_run=dry_run,
            handoff=handoff,
        )
        _sessions[session_id]["status"] = result.status
        _sessions[session_id]["steps"] = result.steps
        _sessions[session_id]["token_usage"] = result.token_usage
        _sessions[session_id]["result_summary"] = result.result_summary
    except Exception as exc:
        _sessions[session_id]["status"] = "failed"
        _sessions[session_id]["result_summary"] = str(exc)


# ── POST /run ──────────────────────────────────────────────────────────────────

@router.post("/run", response_model=RunResponse)
async def start_orchestrator(
    request: RunRequest,
    background_tasks: BackgroundTasks,
) -> RunResponse:
    """
    Start a new Orchestrator session in the background.

    Returns immediately with a session_id.
    Poll GET /status/{session_id} every 2s to see reasoning log updates.

    WHY NOT WAIT FOR THE RESULT?
      The orchestrator could run for 30–120 seconds (multiple Claude API calls +
      real agent runs). HTTP requests shouldn't be kept open that long — browsers
      and proxies will time out. Returning the session_id immediately and polling
      is the right UX pattern for long-running operations.
    """
    session_id = str(uuid.uuid4())

    # Seed the in-memory cache so GET /status/{id} returns something immediately.
    # dry_run is stored here so resume() can use the same flag as run() — the
    # background tasks run at different times and can't share settings state.
    _sessions[session_id] = {
        "session_id": session_id,
        "db_session_id": session_id,   # will be updated when run() creates the DB record
        "status": "running",
        "goal": request.goal,
        "dry_run": request.dry_run,
        "mode": request.mode,
        "handoff": request.handoff,
        "max_apply": request.max_apply,
        "steps": [],
        "pending_jobs": [],
        "token_usage": 0,
        "result_summary": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    background_tasks.add_task(_run_orchestrator, session_id, request.goal, request.dry_run, request.mode, request.max_apply)

    return RunResponse(session_id=session_id, status="started")


# ── GET /status/{id} ──────────────────────────────────────────────────────────

@router.get("/status/{session_id}", response_model=StatusResponse)
async def get_session_status(
    session_id: str,
    db: AsyncSession = Depends(get_db),
) -> StatusResponse:
    """
    Return the current state of an Orchestrator session.

    First checks the in-memory cache (fast path for active sessions).
    Falls back to the DB for sessions that predate this server process.

    POLLING STRATEGY:
      - While status == "running": poll every 2 seconds
      - When status changes to "waiting_for_approval": show approval panel
      - When status == "complete" or "failed": stop polling
    """
    # Fast path: check in-memory cache
    if session_id in _sessions:
        state = _sessions[session_id]
        db_session_id = state.get("db_session_id", session_id)

        # Load pending jobs for the approval panel.
        # pending_job_ids stores full job dicts (id, title, company, score, url)
        # — not just IDs — so we can display cards without a DB lookup.
        # This also handles dry_run mode where IDs are not real DB UUIDs.
        pending_jobs = []
        if state["status"] == "waiting_for_approval":
            db_record = await db.get(OrchestratorSession, uuid.UUID(db_session_id))
            if db_record and db_record.pending_job_ids:
                pending_jobs = _build_pending_jobs(db_record.pending_job_ids)

        return StatusResponse(
            session_id=session_id,
            status=state["status"],
            goal=state["goal"],
            steps=state.get("steps", []),
            pending_jobs=pending_jobs,
            token_usage=state.get("token_usage", 0),
            result_summary=state.get("result_summary"),
            created_at=state.get("created_at"),
        )

    # Slow path: load from DB for sessions not in memory
    # Try the UUID directly first
    try:
        db_record = await db.get(OrchestratorSession, uuid.UUID(session_id))
    except (ValueError, Exception):
        db_record = None

    if not db_record:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    pending_jobs = []
    if db_record.status == SessionStatus.WAITING_FOR_APPROVAL and db_record.pending_job_ids:
        pending_jobs = _build_pending_jobs(db_record.pending_job_ids)

    return StatusResponse(
        session_id=str(db_record.id),
        status=db_record.status.value,
        goal=db_record.goal,
        steps=db_record.steps or [],
        pending_jobs=pending_jobs,
        token_usage=db_record.token_usage,
        result_summary=db_record.result_summary,
        created_at=db_record.created_at.isoformat() if db_record.created_at else None,
    )


# ── POST /approve/{id} ────────────────────────────────────────────────────────

@router.post("/approve/{session_id}", response_model=RunResponse)
async def approve_session(
    session_id: str,
    request: ApproveRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> RunResponse:
    """
    Approve the pending job list and trigger the Apply Agent.

    VALIDATION:
      - 404 if the session doesn't exist
      - 409 if the session is not waiting_for_approval

    AFTER APPROVAL:
      The session status changes to "running" again while the Apply Agent works.
      Poll GET /status/{id} to track progress. When complete, it becomes "complete".
    """
    # Resolve the DB session ID — might differ from the API session_id if
    # the background task created a new DB record
    db_session_id = session_id
    if session_id in _sessions:
        db_session_id = _sessions[session_id].get("db_session_id", session_id)

    # Load and validate the DB record
    try:
        db_record = await db.get(OrchestratorSession, uuid.UUID(db_session_id))
    except (ValueError, Exception):
        db_record = None

    if not db_record:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    if db_record.status != SessionStatus.WAITING_FOR_APPROVAL:
        raise HTTPException(
            status_code=409,
            detail=f"Session is not waiting for approval (current status: {db_record.status.value})",
        )

    # Determine which job IDs to approve.
    # pending_job_ids stores full dicts ({id, title, company, score}) since the
    # approval-gate fix — extract just the id string from each item.
    if request.approved_job_ids is not None:
        approved_ids = request.approved_job_ids
    else:
        # Default: approve all pending jobs
        raw = db_record.pending_job_ids or []
        approved_ids = [
            item["id"] if isinstance(item, dict) else item
            for item in raw
        ]

    # Update in-memory state
    if session_id in _sessions:
        _sessions[session_id]["status"] = "running"

    # Read dry_run and handoff from the session cache (set at POST /run time).
    # Falls back to False — real sessions without a cache entry get real apply.
    session_cache = _sessions.get(session_id, {})
    dry_run = session_cache.get("dry_run", False)
    handoff = session_cache.get("handoff", False)

    background_tasks.add_task(
        _resume_orchestrator, session_id, db_session_id, approved_ids, dry_run, handoff
    )

    return RunResponse(session_id=session_id, status="started")


# ── GET /history ──────────────────────────────────────────────────────────────

@router.get("/history", response_model=list[HistoryItem])
async def get_session_history(
    limit: int = 20,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
) -> list[HistoryItem]:
    """
    Return past Orchestrator sessions, most recent first.

    Reads from the DB (not the in-memory cache) so it includes sessions
    from previous server processes. Useful for reviewing what the agent did
    over the past week/month.

    Args:
        limit:  Max records to return (default 20).
        offset: Pagination offset (skip this many records).
    """
    rows = await db.execute(
        select(OrchestratorSession)
        .order_by(OrchestratorSession.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    sessions = rows.scalars().all()

    return [
        HistoryItem(
            session_id=str(s.id),
            status=s.status.value,
            goal=s.goal,
            token_usage=s.token_usage,
            result_summary=s.result_summary,
            created_at=s.created_at.isoformat() if s.created_at else None,
        )
        for s in sessions
    ]


# ── Helper ─────────────────────────────────────────────────────────────────────

def _build_pending_jobs(pending_job_ids: list) -> list[PendingJob]:
    """
    Build PendingJob objects from the stored pending_job_ids field.

    pending_job_ids stores full job detail dicts (id, title, company, score, url)
    rather than just IDs. This is set by _extract_pending_job_details() in
    orchestrator.py when the approval gate fires.

    WHY DICTS INSTEAD OF IDs?
      The original design stored only job ID strings and did a DB lookup here.
      That broke in dry_run mode (mock IDs like "mock-uuid-1" aren't real UUIDs)
      and added an unnecessary DB round-trip for real sessions.

      Storing full details at approval-gate time means the status endpoint can
      serve the approval panel from the DB record alone — no extra queries.
    """
    result = []
    for item in pending_job_ids:
        if isinstance(item, dict):
            result.append(PendingJob(
                id=item.get("id", ""),
                title=item.get("title", "Unknown"),
                company=item.get("company", "Unknown"),
                score=item.get("score"),
                url=item.get("url"),
            ))
        elif isinstance(item, str):
            # Backwards compat: old sessions stored plain ID strings
            result.append(PendingJob(id=item, title="Unknown", company="Unknown"))
    return result
