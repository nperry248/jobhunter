"""
agents/scraper.py — The Scraper Agent.

RESPONSIBILITY:
  Pull job listings from Greenhouse and Lever public APIs, apply user-defined
  filters, and upsert matching jobs into the PostgreSQL database.

  This agent does ONE thing: find jobs and store them. It does not score,
  apply, or do anything else. That's the other agents' jobs.

ENTRY POINTS:
  1. Programmatic (called by Orchestrator or Celery):
       from agents.scraper import run, ScraperFilters
       result = await run(filters=ScraperFilters(...))

  2. CLI (for manual runs / dry-runs):
       python -m agents.scraper --dry-run --job-type internship

HOW CELERY WILL CALL THIS (future):
  @celery_app.task
  def scraper_task(filters_dict: dict) -> dict:
      filters = ScraperFilters(**filters_dict)
      return asyncio.run(run(filters))

  The run() function is already stateless — no changes needed when we add Celery.
"""

import asyncio
import sys
from dataclasses import dataclass, field, asdict

import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from agents.scraper_parsers import (
    ParsedJob,
    ScraperFilters,
    parse_greenhouse_response,
    parse_lever_response,
    passes_filters,
)
from core.config import settings
from core.database import AsyncSessionLocal
from core.logging_config import get_logger
from models.job import Job, JobStatus

logger = get_logger("scraper")

# ── API URL Templates ─────────────────────────────────────────────────────────
# `?content=true` on Greenhouse includes job descriptions in the list response,
# saving a second API call per job.
_GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
_LEVER_URL = "https://api.lever.co/v0/postings/{slug}?mode=json"


# ── ScraperResult ─────────────────────────────────────────────────────────────
@dataclass
class ScraperResult:
    """Summary of what happened during a scrape run. Returned by run()."""
    total_fetched: int = 0      # raw jobs pulled from APIs before filtering
    total_passed_filter: int = 0  # jobs that survived the filter
    total_new: int = 0          # newly inserted into DB (wasn't there before)
    total_duplicate: int = 0    # already existed in DB (skipped)
    total_errors: int = 0       # companies that failed to fetch
    errors: list[str] = field(default_factory=list)  # error messages for logging


# ── HTTP Fetching with Retry ──────────────────────────────────────────────────
async def fetch_with_retry(
    url: str,
    client: httpx.AsyncClient,
) -> dict | list:
    """
    Fetch a URL with exponential backoff retry logic.

    CONCEPT — Exponential Backoff:
      On failure, we wait before retrying. The wait time doubles each attempt:
        attempt 0 fails → sleep 1s  (base_delay * 2^0)
        attempt 1 fails → sleep 2s  (base_delay * 2^1)
        attempt 2 fails → sleep 4s  (base_delay * 2^2)
      This prevents hammering a rate-limited or struggling server.
      After max_retry_attempts, we give up and re-raise the last exception.

    Args:
        url:    The full URL to GET.
        client: A reused httpx.AsyncClient (connection pooling at HTTP level).

    Returns:
        Parsed JSON response (dict or list depending on the API).

    Raises:
        httpx.HTTPError: if all retry attempts are exhausted.
    """
    last_exception: Exception | None = None

    for attempt in range(settings.max_retry_attempts):
        try:
            response = await client.get(url)
            # raise_for_status() raises httpx.HTTPStatusError on 4xx/5xx responses.
            # We want to retry on 429 (rate limited) and 5xx (server error),
            # but NOT on 404 (company slug doesn't exist — permanent failure).
            if response.status_code == 404:
                logger.warning(
                    "Resource not found, skipping (no retry)",
                    extra={"agent_name": "scraper", "url": url, "status": 404},
                )
                raise httpx.HTTPStatusError(
                    "404 Not Found", request=response.request, response=response
                )
            response.raise_for_status()
            return response.json()

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise  # 404 is permanent — don't retry
            last_exception = e
        except (httpx.NetworkError, httpx.TimeoutException) as e:
            last_exception = e

        # Don't sleep after the final attempt — we're about to raise anyway.
        if attempt < settings.max_retry_attempts - 1:
            delay = settings.retry_base_delay * (2 ** attempt)
            logger.warning(
                "Request failed, retrying",
                extra={
                    "agent_name": "scraper",
                    "url": url,
                    "attempt": attempt + 1,
                    "retry_in_seconds": delay,
                    "error": str(last_exception),
                },
            )
            await asyncio.sleep(delay)

    raise last_exception  # type: ignore[misc]


# ── Fetch Jobs from Each Platform ─────────────────────────────────────────────
async def fetch_greenhouse_jobs(
    slug: str,
    company_name: str,
    client: httpx.AsyncClient,
) -> list[ParsedJob]:
    """
    Fetch and parse all jobs for one Greenhouse company slug.

    Returns an empty list on any error (logged above in fetch_with_retry).
    We never propagate exceptions from individual companies — one broken
    company slug shouldn't stop the whole run.
    """
    url = _GREENHOUSE_URL.format(slug=slug)
    try:
        data = await fetch_with_retry(url, client)
        jobs = parse_greenhouse_response(data, company_name)
        logger.info(
            "Fetched Greenhouse jobs",
            extra={
                "agent_name": "scraper",
                "company": company_name,
                "slug": slug,
                "count": len(jobs),
            },
        )
        return jobs
    except Exception as e:
        logger.error(
            "Failed to fetch Greenhouse jobs",
            extra={
                "agent_name": "scraper",
                "company": company_name,
                "slug": slug,
                "error": str(e),
            },
        )
        return []


async def fetch_lever_jobs(
    slug: str,
    company_name: str,
    client: httpx.AsyncClient,
) -> list[ParsedJob]:
    """Fetch and parse all jobs for one Lever company slug."""
    url = _LEVER_URL.format(slug=slug)
    try:
        data = await fetch_with_retry(url, client)
        # Lever returns a list directly (not wrapped in a dict)
        jobs = parse_lever_response(data if isinstance(data, list) else [], company_name)
        logger.info(
            "Fetched Lever jobs",
            extra={
                "agent_name": "scraper",
                "company": company_name,
                "slug": slug,
                "count": len(jobs),
            },
        )
        return jobs
    except Exception as e:
        logger.error(
            "Failed to fetch Lever jobs",
            extra={
                "agent_name": "scraper",
                "company": company_name,
                "slug": slug,
                "error": str(e),
            },
        )
        return []


# ── Database Upsert ───────────────────────────────────────────────────────────
async def upsert_job(
    parsed: ParsedJob,
    session: AsyncSession,
) -> bool:
    """
    Insert a job into the DB. If a job with the same source_url already exists,
    do nothing (no update, no error).

    CONCEPT — PostgreSQL UPSERT:
      Standard INSERT fails with an error if a UNIQUE constraint is violated.
      INSERT ... ON CONFLICT DO NOTHING silently skips the insert instead.
      This is atomic — safe even if 10 workers try to insert the same job at once.
      Only ONE of them will succeed; the rest silently do nothing.

    Args:
        parsed:  The ParsedJob to insert.
        session: The active DB session (managed by the caller).

    Returns:
        True if the row was newly inserted; False if it already existed.
    """
    stmt = (
        pg_insert(Job)
        .values(
            title=parsed.title,
            company=parsed.company,
            source_url=parsed.source_url,
            source=parsed.source,
            location=parsed.location,
            description=parsed.description,
            status=JobStatus.NEW,
        )
        # on_conflict_do_nothing: if source_url already exists, skip silently.
        # index_elements must match the UniqueConstraint defined in models/job.py.
        .on_conflict_do_nothing(index_elements=["source_url"])
    )

    result = await session.execute(stmt)

    # rowcount == 1 → row was inserted (new job)
    # rowcount == 0 → conflict occurred (duplicate, skipped)
    was_inserted = result.rowcount == 1

    if was_inserted:
        logger.info(
            "Job upserted (new)",
            extra={
                "agent_name": "scraper",
                "company": parsed.company,
                "title": parsed.title,
                "source": parsed.source.value,
                "source_url": parsed.source_url,
            },
        )
    else:
        logger.debug(
            "Job already exists (duplicate skipped)",
            extra={
                "agent_name": "scraper",
                "source_url": parsed.source_url,
            },
        )

    return was_inserted


# ── Main Entry Point ──────────────────────────────────────────────────────────
async def run(
    filters: ScraperFilters | None = None,
    dry_run: bool = False,
    session: AsyncSession | None = None,
) -> ScraperResult:
    """
    Main entry point for the Scraper Agent.

    Fetches jobs from all configured companies, applies filters, and upserts
    matching jobs into the database.

    Args:
        filters: Scraping and filtering configuration. Defaults to settings-based config.
        dry_run: If True, print results without writing to the DB.
        session: Optional DB session. If None, a new session is created internally.
                 Pass a session explicitly in tests so we can use the test DB.

    Returns:
        ScraperResult with counts of fetched, filtered, new, and duplicate jobs.
    """
    # Build default filters from settings if none provided
    if filters is None:
        filters = ScraperFilters(
            greenhouse_slugs=settings.greenhouse_slugs,
            lever_slugs=settings.lever_slugs,
            max_jobs=settings.scraper_max_jobs_per_run,
        )

    result = ScraperResult()

    logger.info(
        "Scraper run starting",
        extra={
            "agent_name": "scraper",
            "dry_run": dry_run,
            "job_type": filters.job_type,
            "keywords": filters.keywords,
            "greenhouse_companies": list(filters.greenhouse_slugs.keys()),
            "lever_companies": list(filters.lever_slugs.keys()),
        },
    )

    # ── Fetch all jobs from all companies ─────────────────────────────────────
    # We use a single httpx.AsyncClient for the entire run.
    # WHY: AsyncClient maintains a connection pool internally. Reusing it means
    # we don't open/close a TCP connection for every single API call.
    all_parsed: list[ParsedJob] = []

    async with httpx.AsyncClient(
        timeout=settings.scraper_request_timeout,
        headers={"User-Agent": "JobHunterAI/1.0 (job search automation)"},
        follow_redirects=True,
    ) as client:
        # Fetch Greenhouse companies
        for slug, company_name in filters.greenhouse_slugs.items():
            jobs = await fetch_greenhouse_jobs(slug, company_name, client)
            all_parsed.extend(jobs)
            if len(all_parsed) >= filters.max_jobs:
                break

        # Fetch Lever companies (if we haven't hit the cap)
        if len(all_parsed) < filters.max_jobs:
            for slug, company_name in filters.lever_slugs.items():
                jobs = await fetch_lever_jobs(slug, company_name, client)
                all_parsed.extend(jobs)
                if len(all_parsed) >= filters.max_jobs:
                    break

    result.total_fetched = len(all_parsed)

    # ── Apply filters ─────────────────────────────────────────────────────────
    passing_jobs = [job for job in all_parsed if passes_filters(job, filters)]
    # Enforce the max_jobs cap after filtering
    passing_jobs = passing_jobs[: filters.max_jobs]
    result.total_passed_filter = len(passing_jobs)

    logger.info(
        "Filter pass complete",
        extra={
            "agent_name": "scraper",
            "total_fetched": result.total_fetched,
            "total_passed": result.total_passed_filter,
        },
    )

    # ── Dry run — print and exit without touching the DB ──────────────────────
    if dry_run:
        print(f"\n{'─' * 60}")
        print(f"DRY RUN — {result.total_passed_filter} jobs passed filters (not saved)")
        print(f"{'─' * 60}")
        for job in passing_jobs:
            print(f"  [{job.source.value.upper():10}] {job.company:20} | {job.title}")
            if job.location:
                print(f"{'':35} {job.location}")
        print(f"{'─' * 60}\n")
        return result

    # ── Upsert into DB ────────────────────────────────────────────────────────
    # If a session was passed in (e.g. from tests), use it.
    # If not, create our own session for this run.
    # NOTE: We use a different code path for the two cases because the
    # externally-provided session's commit/rollback is managed by the caller.
    if session is not None:
        await _upsert_all(passing_jobs, session, result)
        await session.flush()
    else:
        async with AsyncSessionLocal() as session:
            await _upsert_all(passing_jobs, session, result)
            await session.commit()

    logger.info(
        "Scraper run complete",
        extra={
            "agent_name": "scraper",
            "total_fetched": result.total_fetched,
            "total_passed_filter": result.total_passed_filter,
            "total_new": result.total_new,
            "total_duplicate": result.total_duplicate,
            "total_errors": result.total_errors,
        },
    )

    return result


async def _upsert_all(
    jobs: list[ParsedJob],
    session: AsyncSession,
    result: ScraperResult,
) -> None:
    """Upsert all passing jobs into the DB, updating the result counters."""
    for parsed in jobs:
        try:
            was_new = await upsert_job(parsed, session)
            if was_new:
                result.total_new += 1
            else:
                result.total_duplicate += 1
        except Exception as e:
            result.total_errors += 1
            result.errors.append(f"{parsed.company} — {parsed.title}: {e}")
            logger.error(
                "Failed to upsert job",
                extra={
                    "agent_name": "scraper",
                    "company": parsed.company,
                    "title": parsed.title,
                    "error": str(e),
                },
            )


# ── CLI Entry Point ───────────────────────────────────────────────────────────
# This block only runs when you execute the file directly:
#   python -m agents.scraper --dry-run
# It does NOT run when the module is imported (e.g. by tests or Celery).
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="JobHunter Scraper Agent — fetch and store job listings",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print matching jobs without saving to the database",
    )
    parser.add_argument(
        "--job-type",
        choices=["internship", "new_grad", "any"],
        default="any",
        help="Filter by job type (default: any)",
    )
    parser.add_argument(
        "--keywords",
        nargs="*",
        default=[],
        metavar="KEYWORD",
        help="Only include jobs whose title contains at least one keyword",
    )
    parser.add_argument(
        "--locations",
        nargs="*",
        default=[],
        metavar="LOCATION",
        help="Only include jobs in these locations (substring match)",
    )

    args = parser.parse_args()

    filters = ScraperFilters(
        job_type=args.job_type,
        keywords=args.keywords,
        locations=args.locations,
        greenhouse_slugs=settings.greenhouse_slugs,
        lever_slugs=settings.lever_slugs,
        max_jobs=settings.scraper_max_jobs_per_run,
    )

    result = asyncio.run(run(filters=filters, dry_run=args.dry_run))

    print(
        f"\nResult: {result.total_new} new, "
        f"{result.total_duplicate} duplicate, "
        f"{result.total_errors} errors "
        f"(from {result.total_fetched} fetched, "
        f"{result.total_passed_filter} passed filters)"
    )
    sys.exit(0 if result.total_errors == 0 else 1)
