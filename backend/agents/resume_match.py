"""
agents/resume_match.py — The Resume Match Agent.

RESPONSIBILITY:
  For every job in the DB with status="new", ask Claude to score how well the
  user's resume matches that job (0–100). Write the score and reasoning back to
  the DB and update the job's status to "scored".

ENTRY POINTS:
  1. Programmatic (called by Orchestrator or Celery):
       from agents.resume_match import run
       result = await run(resume_path="/path/to/resume.pdf")

  2. CLI (for manual runs / dry-runs):
       python -m agents.resume_match --resume /path/to/resume.pdf --dry-run

ARCHITECTURE — WHY TWO FILES:
  This file is the "Imperative Shell" — it orchestrates side effects:
    - DB reads/writes (fetch_new_jobs, update_job_score)
    - Claude API calls (score_job)
    - Async scheduling (asyncio.to_thread)

  agents/resume_match_logic.py is the "Functional Core" — pure functions:
    - build_scoring_prompt: constructs the Claude prompt
    - parse_claude_response: parses the JSON response
    - clamp_score: bounds-checks the score value
  Those functions are trivially unit-testable with no mocking needed.

CONCEPT — asyncio.to_thread():
  The Anthropic Python SDK (v0.30.0) is SYNCHRONOUS — it uses the `requests`
  library under the hood, which blocks the thread while waiting for the HTTP response.

  If we called it directly inside an `async def`, it would BLOCK THE ENTIRE EVENT LOOP
  for the duration of the API call (often 1–3 seconds), freezing all other requests.

  asyncio.to_thread(fn, *args) runs `fn(*args)` in a thread pool (not the event loop),
  and returns an awaitable. The event loop stays free to handle other work while
  Claude thinks. This is the standard pattern for "sync library inside async code."
"""

import asyncio
import sys
import uuid
from dataclasses import dataclass, field

import anthropic
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agents.resume_match_logic import (
    MatchConfig,
    build_scoring_prompt,
    clamp_score,
    parse_claude_response,
)
from core.config import settings
from core.database import AsyncSessionLocal
from core.logging_config import get_logger
from models.job import Job, JobStatus
from models.user_profile import UserProfile
from services.resume_parser import parse_pdf, strip_html

logger = get_logger("resume_match")


# ── Result Summary ────────────────────────────────────────────────────────────

@dataclass
class MatchResult:
    """Summary of what happened during a resume match run. Returned by run()."""
    total_jobs_fetched: int = 0    # jobs with status=new at start of run
    total_scored: int = 0          # jobs successfully scored and saved
    total_skipped: int = 0         # jobs skipped (empty description, etc.)
    total_errors: int = 0          # jobs where scoring failed
    errors: list[str] = field(default_factory=list)


# ── Resume Loading ────────────────────────────────────────────────────────────

async def load_resume_text(
    resume_path: str | None,
    session: AsyncSession,
) -> str:
    """
    Load resume text, preferring cached text in UserProfile over re-parsing the PDF.

    STRATEGY:
      1. If resume_path is provided explicitly (CLI --resume flag), parse that PDF.
         Also cache the result back into UserProfile.resume_text so next run is faster.
      2. If no path given, check UserProfile.resume_text (already extracted).
      3. If UserProfile has a resume_path, parse that PDF and cache it.
      4. If nothing is available, raise ValueError — we can't score without a resume.

    WHY CACHE:
      PDF parsing with pdfminer is CPU-intensive and takes ~0.2–2 seconds per file.
      With 100 jobs to score, that's 200s of wasted CPU if we re-parse every time.
      Caching in the DB means we parse once and reuse forever (until the file changes).

    Args:
        resume_path: Explicit path override (from CLI --resume flag). None = use DB.
        session:     Active DB session for reading/writing UserProfile.

    Returns:
        Extracted resume text as a plain string.

    Raises:
        ValueError: If no resume source can be found.
    """
    # Load the single UserProfile row (there's only ever one in our single-user system)
    result = await session.execute(select(UserProfile).limit(1))
    profile = result.scalar_one_or_none()

    # Case 1: Explicit path provided — parse PDF directly
    if resume_path:
        logger.info(
            "Parsing resume from explicit path",
            extra={"agent_name": "resume_match", "path": resume_path},
        )
        text = parse_pdf(resume_path)

        # Cache the extracted text back into the DB so future runs skip parsing
        if profile is None:
            profile = UserProfile(resume_path=resume_path, resume_text=text)
            session.add(profile)
        else:
            profile.resume_path = resume_path
            profile.resume_text = text

        await session.flush()  # Write to DB without committing the transaction yet
        return text

    # Case 2: No explicit path — check the DB profile
    if profile is None:
        raise ValueError(
            "No UserProfile found and no --resume path provided. "
            "Create a UserProfile row or pass --resume /path/to/resume.pdf"
        )

    if profile.resume_text:
        logger.info(
            "Using cached resume text from UserProfile",
            extra={"agent_name": "resume_match"},
        )
        return profile.resume_text

    if profile.resume_path:
        logger.info(
            "Parsing resume from UserProfile.resume_path",
            extra={"agent_name": "resume_match", "path": profile.resume_path},
        )
        text = parse_pdf(profile.resume_path)
        profile.resume_text = text  # Cache for next run
        await session.flush()
        return text

    raise ValueError(
        "UserProfile has no resume_path or resume_text. "
        "Upload a resume via the dashboard or pass --resume /path/to/resume.pdf"
    )


# ── Fetch Unscored Jobs ───────────────────────────────────────────────────────

async def fetch_new_jobs(session: AsyncSession) -> list[Job]:
    """
    Fetch all jobs with status=NEW from the database.

    WHY status=NEW ONLY:
      We don't re-score jobs that already have a status of "scored", "reviewed",
      "applied", etc. Those have already been processed. If a job description changes
      and needs re-scoring, manually reset its status to "new" in the DB.

    ORDERING: We order by created_at DESC so recently scraped jobs are scored first.
    This matters when there are thousands of unscored jobs after a fresh scrape —
    the most relevant (newest) ones get scored in the current run.

    Args:
        session: Active DB session.

    Returns:
        List of Job ORM objects, all with status=NEW.
    """
    result = await session.execute(
        select(Job)
        .where(Job.status == JobStatus.NEW)
        .where(Job.deleted_at.is_(None))    # Respect soft deletes
        .order_by(Job.created_at.desc())
    )
    return list(result.scalars().all())


# ── Score One Job via Claude ──────────────────────────────────────────────────

def score_job(
    resume_text: str,
    job: Job,
    config: MatchConfig,
) -> tuple[float, str]:
    """
    Call the Claude API to score one job against the resume.

    IMPORTANT — THIS FUNCTION IS SYNCHRONOUS:
      The Anthropic SDK is sync-only (uses `requests` internally).
      This function MUST be called via `asyncio.to_thread(score_job, ...)` from
      async code, never awaited directly. See run() below for usage.

    HOW IT WORKS:
      1. Build the scoring prompt using build_scoring_prompt() (pure function)
      2. Call claude.messages.create() — this blocks the thread for 1–3s
      3. Extract the text response
      4. Parse the JSON with parse_claude_response() — handles malformed responses
      5. Return (score, reasoning)

    Args:
        resume_text: Plain text of the resume.
        job:         The Job ORM object to score.
        config:      Scoring configuration (model, token limit, rubric, etc.).

    Returns:
        (score, reasoning) — score is 0.0–100.0, reasoning is a one-sentence string.
    """
    # Strip HTML from job description (Greenhouse/Lever return HTML markup)
    clean_description = strip_html(job.description or "")

    prompt = build_scoring_prompt(
        resume_text=resume_text,
        job_title=job.title,
        job_company=job.company,
        job_description=clean_description,
        config=config,
    )

    # NOTE: We create a new Anthropic client per call rather than a shared instance.
    # WHY: The client is stateless — it just holds the API key. Since score_job()
    # runs in a thread pool, a shared client would need to be thread-safe.
    # Creating one per call is slightly less efficient but simpler and safer.
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    message = client.messages.create(
        model=config.model,
        max_tokens=config.max_tokens,
        messages=[
            {"role": "user", "content": prompt},
        ],
    )

    # The response content is a list of content blocks. We want the first text block.
    response_text = message.content[0].text if message.content else ""

    score, reasoning = parse_claude_response(response_text)

    logger.info(
        "Job scored",
        extra={
            "agent_name": "resume_match",
            "job_id": str(job.id),
            "company": job.company,
            "title": job.title,
            "score": score,
        },
    )

    return score, reasoning


# ── Write Score to DB ─────────────────────────────────────────────────────────

async def update_job_score(
    job_id: uuid.UUID,
    score: float,
    reasoning: str,
    session: AsyncSession,
) -> None:
    """
    Write the match score and reasoning to the DB and advance the job's status.

    WHY status → SCORED:
      The orchestrator uses job status to decide what to do next.
      "new" = needs scoring. "scored" = ready for the Apply Agent to consider.
      Updating status here ensures jobs aren't re-scored on the next run.

    NOTE: We use a raw UPDATE statement (not load-then-save) to avoid race conditions.
      If two workers ran simultaneously (won't happen now, but could in the future),
      load-then-save is a classic read-modify-write race. The UPDATE statement is atomic.

    Args:
        job_id:    UUID of the job to update.
        score:     Match score 0.0–100.0.
        reasoning: One-sentence explanation from Claude.
        session:   Active DB session.
    """
    await session.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(
            match_score=clamp_score(score),
            match_reasoning=reasoning,
            status=JobStatus.SCORED,
        )
    )


# ── Main Entry Point ──────────────────────────────────────────────────────────

async def run(
    resume_path: str | None = None,
    dry_run: bool = False,
    session: AsyncSession | None = None,
) -> MatchResult:
    """
    Main entry point for the Resume Match Agent.

    Loads the resume, fetches all unscored jobs, and scores each one via Claude.
    Writes scores back to the DB unless dry_run=True.

    CONCURRENCY NOTE:
      We score jobs SEQUENTIALLY (not in parallel) because:
        1. Claude API rate limits would cause 429 errors on burst parallel calls.
        2. asyncio.to_thread() keeps the event loop free, so single-threaded is fine.
        3. settings.claude_request_delay_seconds adds a polite pause between calls.

      If you need parallel scoring in the future, use asyncio.Semaphore to cap
      concurrency at e.g. 5 parallel calls.

    Args:
        resume_path: Path to a PDF resume. If None, loads from UserProfile in DB.
        dry_run:     If True, print scores without saving to the DB.
        session:     Optional DB session. Pass explicitly in tests.

    Returns:
        MatchResult with counts of scored, skipped, and failed jobs.
    """
    result = MatchResult()
    config = MatchConfig()

    logger.info(
        "Resume Match Agent run starting",
        extra={
            "agent_name": "resume_match",
            "dry_run": dry_run,
            "resume_path": resume_path or "(from DB profile)",
        },
    )

    # ── Session management ────────────────────────────────────────────────────
    # Same pattern as scraper.py: use the injected session (tests) or create our own.
    if session is not None:
        await _run_with_session(resume_path, dry_run, config, result, session)
        await session.flush()
    else:
        async with AsyncSessionLocal() as session:
            await _run_with_session(resume_path, dry_run, config, result, session)
            await session.commit()

    logger.info(
        "Resume Match Agent run complete",
        extra={
            "agent_name": "resume_match",
            "total_fetched": result.total_jobs_fetched,
            "total_scored": result.total_scored,
            "total_skipped": result.total_skipped,
            "total_errors": result.total_errors,
        },
    )

    return result


async def _run_with_session(
    resume_path: str | None,
    dry_run: bool,
    config: MatchConfig,
    result: MatchResult,
    session: AsyncSession,
) -> None:
    """
    Inner implementation of the scoring loop. Extracted to allow clean session
    management in run() regardless of whether a session is injected or created.
    """
    # ── Load resume ───────────────────────────────────────────────────────────
    try:
        resume_text = await load_resume_text(resume_path, session)
    except (ValueError, FileNotFoundError) as e:
        logger.error(
            "Could not load resume — aborting run",
            extra={"agent_name": "resume_match", "error": str(e)},
        )
        result.errors.append(f"Resume load failed: {e}")
        result.total_errors += 1
        return

    # ── Fetch unscored jobs ───────────────────────────────────────────────────
    jobs = await fetch_new_jobs(session)
    result.total_jobs_fetched = len(jobs)

    logger.info(
        "Fetched unscored jobs",
        extra={"agent_name": "resume_match", "count": len(jobs)},
    )

    if not jobs:
        logger.info(
            "No new jobs to score — exiting",
            extra={"agent_name": "resume_match"},
        )
        return

    # ── Score each job ────────────────────────────────────────────────────────
    if dry_run:
        print(f"\n{'─' * 60}")
        print(f"DRY RUN — would score {len(jobs)} jobs (not saved)")
        print(f"{'─' * 60}")

    for job in jobs:
        # Skip jobs with no description — Claude can't score without context.
        if not job.description or not job.description.strip():
            logger.warning(
                "Skipping job with empty description",
                extra={
                    "agent_name": "resume_match",
                    "job_id": str(job.id),
                    "company": job.company,
                    "title": job.title,
                },
            )
            result.total_skipped += 1
            continue

        try:
            # Run the synchronous Claude call in a thread so we don't block the event loop.
            # CONCEPT — asyncio.to_thread(fn, *args):
            #   This is equivalent to: loop.run_in_executor(None, fn, *args)
            #   It submits score_job() to Python's default ThreadPoolExecutor and
            #   returns a coroutine that the event loop can await without blocking.
            score, reasoning = await asyncio.to_thread(
                score_job, resume_text, job, config
            )

            if dry_run:
                print(
                    f"  [{score:5.1f}] {job.company:20} | {job.title[:50]}"
                )
                print(f"         → {reasoning[:80]}")
                result.total_scored += 1
            else:
                await update_job_score(job.id, score, reasoning, session)
                result.total_scored += 1

        except Exception as e:
            result.total_errors += 1
            error_msg = f"{job.company} — {job.title}: {e}"
            result.errors.append(error_msg)
            logger.error(
                "Failed to score job",
                extra={
                    "agent_name": "resume_match",
                    "job_id": str(job.id),
                    "company": job.company,
                    "title": job.title,
                    "error": str(e),
                },
            )

        # Polite delay between Claude API calls to stay under rate limits.
        # In tests, override settings.claude_request_delay_seconds = 0.0 to skip.
        if settings.claude_request_delay_seconds > 0:
            await asyncio.sleep(settings.claude_request_delay_seconds)

    if dry_run:
        print(f"{'─' * 60}\n")


# ── CLI Entry Point ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Resume Match Agent — score jobs against your resume",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--resume",
        type=str,
        default=None,
        metavar="PATH",
        help="Path to your resume PDF. If omitted, uses the path stored in UserProfile.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print scores without saving them to the database",
    )

    args = parser.parse_args()

    result = asyncio.run(run(resume_path=args.resume, dry_run=args.dry_run))

    print(
        f"\nResult: {result.total_scored} scored, "
        f"{result.total_skipped} skipped, "
        f"{result.total_errors} errors "
        f"(from {result.total_jobs_fetched} new jobs)"
    )
    sys.exit(0 if result.total_errors == 0 else 1)
