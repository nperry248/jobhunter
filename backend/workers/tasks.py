"""
workers/tasks.py — Celery task definitions for the JobHunter pipeline.

CONCEPT — What is a Celery task?
  A task is just a Python function decorated with @celery_app.task.
  That decorator registers the function with Celery so workers can discover
  and run it. When you call `.delay()` on a task, Celery serializes the
  arguments and puts a message on the Redis queue. A worker picks it up
  and calls the function.

CONCEPT — Async agents inside sync Celery tasks:
  Celery tasks are synchronous — they're plain Python functions, not coroutines.
  But our agents (scraper, resume_match) are async — they use `async def` and
  `await` internally because they make many network calls concurrently.

  The bridge: `asyncio.run(coro)`.
  This function:
    1. Creates a new event loop
    2. Runs the coroutine to completion on that loop
    3. Closes the loop and returns the result

  So each task creates its own event loop, runs the full agent pipeline,
  and exits cleanly. No state bleeds between task runs.

  WHY NOT share one event loop across tasks?
    Celery workers are multi-process (by default). Each process has its own
    memory, so sharing an event loop across tasks would require threading,
    which is complex and error-prone. asyncio.run() is simpler and safe.

TASKS DEFINED HERE:
  - scrape_task:           Run the scraper agent (fetch + upsert jobs)
  - score_task:            Run the resume match agent (score new jobs)
  - scrape_and_score_task: Run both in sequence (the normal pipeline)

USAGE:
  # Trigger a task immediately (fire and forget):
  scrape_and_score_task.delay()

  # Trigger with a countdown (run in 60 seconds):
  scrape_and_score_task.apply_async(countdown=60)

  # Run synchronously (useful in tests or scripts):
  scrape_and_score_task.apply()
"""

import asyncio
import dataclasses

from workers.celery_app import celery_app
from core.logging_config import get_logger

logger = get_logger("workers.tasks")


def _to_dict(result) -> dict:
    """
    Convert a dataclass result object to a plain dict for JSON serialization.

    Celery task return values must be JSON-serializable (we set task_serializer="json").
    Our agent run() functions return dataclasses (ScraperResult, MatchResult),
    so we convert them here before returning from the task.
    """
    if dataclasses.is_dataclass(result) and not isinstance(result, type):
        return dataclasses.asdict(result)
    return result  # already a dict or primitive


# ── Scrape Task ────────────────────────────────────────────────────────────────

@celery_app.task(
    name="scrape_task",
    # CONCEPT — max_retries + autoretry_for:
    #   If the task raises one of the listed exceptions, Celery will
    #   automatically retry it up to max_retries times, with an exponential
    #   backoff (retry_backoff=True). This handles transient network errors
    #   without manual retry logic in the task body.
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=True,
    retry_backoff_max=300,  # cap backoff at 5 minutes
)
def scrape_task() -> dict:
    """
    Run the scraper agent: fetch jobs from Greenhouse + Lever and upsert to DB.

    Returns a summary dict with counts (new, duplicate, errors) so the result
    can be inspected in the Celery result backend.
    """
    # Import here (not at module top) to avoid circular imports.
    # Celery imports this module at startup; importing agents at module level
    # would trigger their top-level imports (SQLAlchemy, Playwright, etc.)
    # before the app is fully initialized.
    from agents.scraper import run as scraper_run

    logger.info("Starting scrape_task", extra={"agent_name": "workers.tasks"})
    result = asyncio.run(scraper_run())
    result_dict = _to_dict(result)
    logger.info(
        "scrape_task complete",
        extra={"agent_name": "workers.tasks", **result_dict},
    )
    return result_dict


# ── Score Task ─────────────────────────────────────────────────────────────────

@celery_app.task(
    name="score_task",
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=True,
    retry_backoff_max=300,
)
def score_task(resume_path: str | None = None) -> dict:
    """
    Run the resume match agent: score all 'new' jobs against the resume.

    Args:
        resume_path: Optional explicit path to a resume PDF.
                     If None, the agent reads the path from UserProfile in the DB.

    Returns a summary dict with counts (scored, skipped, errors).
    """
    from agents.resume_match import run as resume_match_run

    logger.info(
        "Starting score_task",
        extra={"agent_name": "workers.tasks", "resume_path": resume_path},
    )
    result = asyncio.run(resume_match_run(resume_path=resume_path))
    result_dict = _to_dict(result)
    logger.info(
        "score_task complete",
        extra={"agent_name": "workers.tasks", **result_dict},
    )
    return result_dict


# ── Scrape + Score Pipeline Task ───────────────────────────────────────────────

@celery_app.task(
    name="scrape_and_score_task",
    autoretry_for=(Exception,),
    max_retries=2,
    retry_backoff=True,
    retry_backoff_max=600,  # cap at 10 minutes for the full pipeline
)
def scrape_and_score_task(resume_path: str | None = None) -> dict:
    """
    Run the full pipeline: scrape jobs, then score them.

    This is the task triggered by the Beat scheduler every hour.
    Running them in one task (rather than chaining two tasks) keeps the
    pipeline simple: if the scraper fails, we don't attempt scoring on
    stale data.

    Args:
        resume_path: Optional explicit path to a resume PDF.

    Returns a combined summary dict.
    """
    from agents.scraper import run as scraper_run
    from agents.resume_match import run as resume_match_run

    logger.info(
        "Starting scrape_and_score_task",
        extra={"agent_name": "workers.tasks", "resume_path": resume_path},
    )

    # ── Step 1: Scrape ─────────────────────────────────────────────────────────
    scrape_result = _to_dict(asyncio.run(scraper_run()))
    logger.info(
        "Scrape step complete",
        extra={"agent_name": "workers.tasks", **scrape_result},
    )

    # ── Step 2: Score ──────────────────────────────────────────────────────────
    score_result = _to_dict(asyncio.run(resume_match_run(resume_path=resume_path)))
    logger.info(
        "Score step complete",
        extra={"agent_name": "workers.tasks", **score_result},
    )

    return {
        "scrape": scrape_result,
        "score": score_result,
    }
