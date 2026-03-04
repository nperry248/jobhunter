"""
api/routes/pipeline.py — Endpoints to trigger and monitor the scrape+score pipeline.

ENDPOINTS:
  POST /api/v1/pipeline/run    — Start the pipeline (scrape → score) in the background
  GET  /api/v1/pipeline/status — Check whether the pipeline is currently running

WHY NOT USE CELERY HERE?
  The Celery tasks in workers/tasks.py require a Celery worker process to be
  running to actually execute. In development you usually don't have that up.
  A "Run Now" button that silently does nothing is terrible UX.

  FastAPI's BackgroundTasks is the right tool here:
    - The endpoint responds immediately ("started")
    - The pipeline runs in the same server process, in the background
    - No extra processes to manage in dev
    - Works identically in production for single-server deployments

  If you ever need to scale to multiple servers, you'd swap background_tasks.add_task()
  for scrape_and_score_task.delay() and point the frontend at Celery task status.
  That's a one-line change.

CONCEPT — In-memory state:
  We track pipeline state in a module-level dict `_state`. This is intentionally
  simple: it resets on server restart, and it's not safe for multi-process
  deployments. For this single-server use case it's exactly right — no Redis,
  no DB table, no complexity.

  If you scale to multiple API servers behind a load balancer, move this state
  to Redis: `redis.set("pipeline:running", "1", ex=3600)`.
"""

import dataclasses
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

router = APIRouter()


# ── In-memory pipeline state ──────────────────────────────────────────────────
# A simple dict that tracks the current + last run state.
# Module-level so it persists across requests (within one server process).

_state: dict = {
    "running": False,
    "started_at": None,       # ISO timestamp when the current/last run started
    "finished_at": None,      # ISO timestamp when the last run completed
    "last_result": None,      # dict with scrape + score summary from last run
    "last_error": None,       # error message string if last run failed
}


# ── Response schemas ──────────────────────────────────────────────────────────

class PipelineStatusResponse(BaseModel):
    running: bool
    started_at: str | None
    finished_at: str | None
    last_result: dict | None
    last_error: str | None


class PipelineTriggerResponse(BaseModel):
    status: str   # "started" or "already_running"


# ── Background task function ──────────────────────────────────────────────────

async def _run_pipeline(resume_path: str | None = None) -> None:
    """
    The actual pipeline logic — runs scraper then resume match agent.

    This is called by FastAPI's BackgroundTasks after the HTTP response is sent.
    Errors are caught and stored in _state so the frontend can display them.

    CONCEPT — Why async?
      Both agent run() functions are async (they do concurrent I/O).
      BackgroundTasks supports async functions natively — FastAPI runs them
      on the same event loop as the web server.
    """
    from agents.scraper import run as scraper_run
    from agents.resume_match import run as resume_match_run

    _state["running"] = True
    _state["started_at"] = datetime.now(timezone.utc).isoformat()
    _state["finished_at"] = None
    _state["last_result"] = None
    _state["last_error"] = None

    try:
        # ── Step 1: Scrape ─────────────────────────────────────────────────────
        scrape_result = await scraper_run()

        # ── Step 2: Score ──────────────────────────────────────────────────────
        score_result = await resume_match_run(resume_path=resume_path)

        _state["last_result"] = {
            "scrape": dataclasses.asdict(scrape_result),
            "score": dataclasses.asdict(score_result),
        }

    except Exception as exc:
        _state["last_error"] = str(exc)

    finally:
        _state["running"] = False
        _state["finished_at"] = datetime.now(timezone.utc).isoformat()


# ── POST /api/v1/pipeline/run ─────────────────────────────────────────────────

@router.post("/run", response_model=PipelineTriggerResponse)
async def trigger_pipeline(background_tasks: BackgroundTasks) -> PipelineTriggerResponse:
    """
    Start the scrape + score pipeline in the background.

    Returns immediately with {"status": "started"}.
    The pipeline runs asynchronously — poll GET /status to track progress.

    Returns {"status": "already_running"} with 409 if a run is already in progress.
    This prevents accidentally stacking multiple concurrent runs.
    """
    if _state["running"]:
        raise HTTPException(
            status_code=409,
            detail="Pipeline is already running. Poll /status to track progress.",
        )

    # CONCEPT — BackgroundTasks.add_task():
    #   This registers the function to run AFTER the response is sent to the client.
    #   The client gets their "started" response immediately, and the pipeline
    #   begins executing without making them wait.
    background_tasks.add_task(_run_pipeline, resume_path=None)

    return PipelineTriggerResponse(status="started")


# ── GET /api/v1/pipeline/status ───────────────────────────────────────────────

@router.get("/status", response_model=PipelineStatusResponse)
async def get_pipeline_status() -> PipelineStatusResponse:
    """
    Return the current pipeline state.

    The frontend polls this every few seconds after triggering a run.
    When `running` transitions from true → false, the run is complete
    and the jobs list should be refreshed.
    """
    return PipelineStatusResponse(**_state)
