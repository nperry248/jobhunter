"""
agents/scraper_parsers.py — Pure parsing and filtering functions for the Scraper Agent.

WHY THIS IS SEPARATE FROM scraper.py:
  This file contains ONLY pure functions — no HTTP calls, no DB writes, no logging.
  Pure functions are easy to test: you pass in data, you get back data, no side effects.
  If we mixed parsing into scraper.py, tests would need a real network and database
  just to test whether a job title is being parsed correctly. That's wasteful.

  Rule of thumb: if a function only transforms data, it belongs here.
  If it talks to the network or DB, it belongs in scraper.py.

WHAT THIS FILE PROVIDES:
  1. ParsedJob       — intermediate data class: clean job data before it hits the DB
  2. ScraperFilters  — what the user wants to scrape (job type, keywords, etc.)
  3. parse_greenhouse_response() — converts raw Greenhouse API JSON → list[ParsedJob]
  4. parse_lever_response()      — converts raw Lever API JSON → list[ParsedJob]
  5. passes_filters()            — decides if a ParsedJob matches the user's criteria
"""

import re
from dataclasses import dataclass, field

from models.job import JobSource

# ── Keywords used for job-type detection ─────────────────────────────────────
# These are matched against job titles (case-insensitive).
# We match on substrings so "Software Engineering Intern" matches "intern".

# Keywords that indicate an internship role
_INTERNSHIP_KEYWORDS: frozenset[str] = frozenset([
    "intern", "internship", "co-op", "coop", "co op",
])

# Keywords that indicate a new-graduate / entry-level role
_NEW_GRAD_KEYWORDS: frozenset[str] = frozenset([
    "new grad", "new graduate", "entry level", "entry-level",
    "junior", "associate", "university grad", "recent grad",
    "early career", "campus",
])


# ── ParsedJob ─────────────────────────────────────────────────────────────────
# CONCEPT — Dataclass:
#   A dataclass is like a regular class but Python auto-generates __init__,
#   __repr__, and __eq__ based on the field annotations. It's a clean way to
#   represent a "bag of data" without writing boilerplate.
#   `@dataclass` is Python's lightweight alternative to Pydantic for internal data.
@dataclass
class ParsedJob:
    """
    Intermediate representation of a scraped job, before it's written to the DB.

    WHY NOT JUST USE THE SQLAlchemy Job MODEL DIRECTLY?
      The Job model is tightly coupled to the DB (sessions, transactions, etc.).
      ParsedJob is a plain Python object — easy to create in tests, easy to pass
      around, no DB dependency. The scraper converts ParsedJob → Job at the last
      step before inserting.
    """
    title: str
    company: str
    source_url: str
    source: JobSource
    location: str | None = None
    description: str | None = None
    external_id: str = ""   # the platform's own ID (Greenhouse int, Lever UUID)


# ── ScraperFilters ────────────────────────────────────────────────────────────
@dataclass
class ScraperFilters:
    """
    The user's filtering criteria for a scrape run.

    All filters are optional — the defaults scrape everything.
    Filters are applied IN ORDER so cheap checks (blocklist) run before
    expensive ones (keyword matching).
    """
    # "internship" → only internship-titled roles
    # "new_grad"   → only entry-level / new grad roles
    # "any"        → no filtering by job type (includes all seniority)
    job_type: str = "any"

    # Job title must contain at least one of these strings (case-insensitive).
    # Empty list = no keyword filter (all titles pass).
    # Example: ["software engineer", "backend", "python"]
    keywords: list[str] = field(default_factory=list)

    # Job location must contain at least one of these strings (case-insensitive).
    # Empty list = no location filter (all locations pass).
    # Example: ["san francisco", "new york", "remote"]
    locations: list[str] = field(default_factory=list)

    # Jobs from companies in this list are skipped entirely.
    # Match is case-insensitive against the company name.
    company_blocklist: list[str] = field(default_factory=list)

    # Companies to query on each platform.
    # Key = URL slug, Value = human-readable name for the DB.
    greenhouse_slugs: dict[str, str] = field(default_factory=dict)
    lever_slugs: dict[str, str] = field(default_factory=dict)

    # Hard cap on total jobs written per run (prevents runaway scraping).
    max_jobs: int = 100

    # Per-company cap on SWE-filtered jobs kept per run.
    # WHY: without this, the first company in the list fills the entire max_jobs
    # quota before any other company gets scraped. A per-company cap guarantees
    # diversity across the target company list.
    max_jobs_per_company: int = 5


# ── Greenhouse Parser ─────────────────────────────────────────────────────────
def parse_greenhouse_response(data: dict, company_name: str) -> list[ParsedJob]:
    """
    Convert a raw Greenhouse API response into a list of ParsedJob objects.

    Greenhouse API endpoint (public, no auth):
      GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true

    The `?content=true` param tells Greenhouse to include the full job description
    in the list response, saving us a second API call per job.

    Args:
        data:         The parsed JSON response dict from the Greenhouse API.
        company_name: Human-readable company name (e.g. "Airbnb") for the DB.

    Returns:
        A list of ParsedJob objects. Empty list if the response has no jobs
        or if the format is unexpected (we never raise — we just skip bad data).
    """
    jobs: list[ParsedJob] = []

    # The Greenhouse response wraps jobs in a "jobs" key.
    # .get() with a default of [] means we return an empty list instead of crashing
    # if the API changes or returns an unexpected shape.
    raw_jobs = data.get("jobs", [])

    for raw in raw_jobs:
        try:
            job = _parse_single_greenhouse_job(raw, company_name)
            if job is not None:
                jobs.append(job)
        except Exception:
            # Skip malformed individual job entries rather than crashing the whole run.
            # The integration layer logs these; parsers don't have a logger.
            continue

    return jobs


def _parse_single_greenhouse_job(raw: dict, company_name: str) -> ParsedJob | None:
    """
    Parse one job dict from a Greenhouse response.

    Returns None if the job is missing required fields (title or URL).
    We use None-return instead of raising so the caller can filter cleanly.
    """
    title = raw.get("title", "").strip()
    source_url = raw.get("absolute_url", "").strip()

    # A job without a title or URL is not useful — skip it.
    if not title or not source_url:
        return None

    # Location is nested: {"name": "San Francisco, CA"} or missing entirely.
    location_obj = raw.get("location") or {}
    location = location_obj.get("name", "").strip() or None

    # Description is included when ?content=true is used.
    # Greenhouse returns it as raw HTML — we store it as-is for now.
    # Phase 2 (Resume Match) will strip the HTML before sending to Claude.
    description = raw.get("content", "").strip() or None

    return ParsedJob(
        title=title,
        company=company_name,
        source_url=source_url,
        source=JobSource.GREENHOUSE,
        location=location,
        description=description,
        external_id=str(raw.get("id", "")),
    )


# ── Lever Parser ──────────────────────────────────────────────────────────────
def parse_lever_response(data: list, company_name: str) -> list[ParsedJob]:
    """
    Convert a raw Lever API response into a list of ParsedJob objects.

    Lever API endpoint (public, no auth):
      GET https://api.lever.co/v0/postings/{slug}?mode=json

    Unlike Greenhouse, Lever returns a flat JSON array (not wrapped in a key),
    and includes the description in the list response by default.

    Args:
        data:         The parsed JSON response — a list of job posting dicts.
        company_name: Human-readable company name for the DB.

    Returns:
        A list of ParsedJob objects.
    """
    jobs: list[ParsedJob] = []

    for raw in data:
        try:
            job = _parse_single_lever_job(raw, company_name)
            if job is not None:
                jobs.append(job)
        except Exception:
            continue

    return jobs


def _parse_single_lever_job(raw: dict, company_name: str) -> ParsedJob | None:
    """Parse one posting dict from a Lever response."""
    title = raw.get("text", "").strip()
    source_url = raw.get("hostedUrl", "").strip()

    if not title or not source_url:
        return None

    # Lever nests location inside "categories"
    categories = raw.get("categories") or {}
    location = categories.get("location", "").strip() or None

    # Lever returns description as HTML in "descriptionPlain" (plain text) or "description" (HTML).
    # We prefer plain text when available.
    description = (
        raw.get("descriptionPlain", "").strip()
        or raw.get("description", "").strip()
        or None
    )

    return ParsedJob(
        title=title,
        company=company_name,
        source_url=source_url,
        source=JobSource.LEVER,
        location=location,
        description=description,
        external_id=raw.get("id", ""),
    )


# ── Filter Logic ──────────────────────────────────────────────────────────────
def passes_filters(job: ParsedJob, filters: ScraperFilters) -> bool:
    """
    Return True if a job passes ALL of the user's active filters.

    Filters are applied cheapest-first to short-circuit early:
    1. Company blocklist (O(n) string compare, very fast)
    2. Job type (regex on title, fast)
    3. Keywords (substring search on title, fast)
    4. Location (substring search, fast)

    Args:
        job:     The parsed job to evaluate.
        filters: The user's filter criteria.

    Returns:
        True if the job should be saved; False if it should be skipped.
    """
    # ── 1. Company blocklist ─────────────────────────────────────────────────
    # Skip if the company is in the user's blocklist (case-insensitive).
    company_lower = job.company.lower()
    for blocked in filters.company_blocklist:
        if blocked.lower() in company_lower:
            return False

    # ── 2. Job type filter ───────────────────────────────────────────────────
    title_lower = job.title.lower()

    if filters.job_type == "internship":
        # Must contain at least one internship keyword in the title.
        if not any(kw in title_lower for kw in _INTERNSHIP_KEYWORDS):
            return False

    elif filters.job_type == "new_grad":
        # Must contain at least one new-grad keyword in the title.
        if not any(kw in title_lower for kw in _NEW_GRAD_KEYWORDS):
            return False

    # job_type == "any" → no filtering, all titles pass

    # ── 3. Keyword filter ────────────────────────────────────────────────────
    # If keywords are specified, at least one must appear in the title.
    if filters.keywords:
        if not any(kw.lower() in title_lower for kw in filters.keywords):
            return False

    # ── 4. Location filter ───────────────────────────────────────────────────
    # If locations are specified, at least one must appear in the job's location string.
    # If the job has no location listed, it passes (remote-first = location unknown).
    if filters.locations and job.location:
        location_lower = job.location.lower()
        if not any(loc.lower() in location_lower for loc in filters.locations):
            return False

    return True
