"""
agents/apply.py — The Apply Agent.

RESPONSIBILITY:
  Read "reviewed" jobs from the DB, navigate to each Greenhouse application URL,
  fill the form using UserProfile data, screenshot the result, and record whether
  the application was submitted or failed.

ENTRY POINTS:
  1. Programmatic (Celery / Orchestrator):
       from agents.apply import run
       result = await run(job_ids=[uuid1, uuid2])

  2. CLI (manual / debug):
       python -m agents.apply --dry-run
       python -m agents.apply --job-ids <uuid1> <uuid2>

DRY RUN MODE:
  Set dry_run=True (or pass --dry-run from CLI, or APPLY_DRY_RUN=true in .env).
  The agent fills every field and screenshots the completed form, but NEVER clicks
  submit. Safe to test against live Greenhouse postings.

ARCHITECTURE — TWO FILES:
  apply_logic.py:  Pure functions (no I/O). Instantly unit-testable with no mocks.
  apply.py (this): Imperative shell — DB queries, Playwright automation, file I/O.

CONCEPT — Playwright browser automation:
  Playwright controls a real Chromium browser from Python. It's like a person
  sitting at a computer, but automated:
    - page.goto(url)         → opens a URL
    - page.fill("#id", val)  → clicks a field and types
    - page.click("button")   → clicks a button
    - page.screenshot(path)  → takes a screenshot

  WHY NOT JUST HTTP REQUESTS?
    Greenhouse forms are JavaScript-heavy. They validate fields client-side
    (e.g. phone format), render resume upload components dynamically, and
    sometimes require JS events to fire before the submit button activates.
    A raw HTTP POST would skip all that — only a real browser handles it correctly.

CONCEPT — Injected page parameter:
  apply_greenhouse() takes a `page: Page` argument instead of creating its own page.
  WHY: This makes it testable without a real browser — tests pass a mock Page object.
  The actual browser lifecycle (launch → context → page → close) lives in run()
  and can be swapped out or mocked independently of the form-filling logic.
"""

import asyncio
import sys
import uuid
from datetime import UTC, datetime

from playwright.async_api import Frame, Page, async_playwright
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agents.apply_logic import (
    ApplyConfig,
    ApplyResult,
    build_optional_field_map,
    get_screenshots_dir,
    screenshot_filename,
    split_full_name,
)
from core.config import settings
from core.database import AsyncSessionLocal
from core.logging_config import get_logger
from models.application import Application, ApplicationStatus
from models.job import Job, JobStatus
from models.user_profile import UserProfile

logger = get_logger("apply")


# ── Profile Loading ─────────────────────────────────────────────────────────────

async def load_profile(session: AsyncSession) -> UserProfile:
    """
    Load the single UserProfile row from the DB.

    WHY FAIL FAST:
      The Apply Agent is useless without a profile — it doesn't know who to apply as.
      Failing immediately with a clear message is better than silently skipping all
      jobs or submitting an application with blank fields.

    WHAT WE CHECK:
      1. Profile exists (at least one row in user_profiles table)
      2. full_name and email are filled in (required on every application form)
      3. resume_path is set (required for file upload)

    Args:
        session: Active DB session.

    Returns:
        The single UserProfile row.

    Raises:
        RuntimeError: If profile is missing or incomplete.
    """
    result = await session.execute(select(UserProfile).limit(1))
    profile = result.scalar_one_or_none()

    if profile is None:
        raise RuntimeError(
            "No UserProfile found in the database. "
            "Fill in your profile in the Settings page before running the Apply Agent."
        )
    if not profile.full_name or not profile.email:
        raise RuntimeError(
            "UserProfile is incomplete: full_name and email are required for applications. "
            "Fill in your profile in the Settings page."
        )
    if not profile.resume_path:
        raise RuntimeError(
            "UserProfile has no resume_path. "
            "Upload your resume in the Settings page before running the Apply Agent."
        )
    return profile


# ── Fetch Reviewed Jobs ─────────────────────────────────────────────────────────

async def fetch_reviewed_jobs(
    session: AsyncSession,
    job_ids: list[uuid.UUID] | None,
) -> list[Job]:
    """
    Fetch jobs to apply to.

    If job_ids are provided: fetch those specific jobs (regardless of current status).
    Otherwise: fetch ALL jobs with status=REVIEWED.

    NOTE: min_score filtering is intentionally NOT done here in SQL.
    WHY: Filtering in Python lets the caller increment result.total_skipped for
    observability — if we filtered in SQL, we'd never know how many were skipped.

    Args:
        session: Active DB session.
        job_ids: Specific job UUIDs to target (or None for all REVIEWED jobs).

    Returns:
        List of Job ORM objects ordered by match_score DESC (best first).
    """
    if job_ids:
        # Specific jobs requested — trust the caller's intent, ignore status
        result = await session.execute(
            select(Job)
            .where(Job.id.in_(job_ids))
            .where(Job.deleted_at.is_(None))
        )
    else:
        # Default: all jobs the user has reviewed and not yet actioned
        result = await session.execute(
            select(Job)
            .where(Job.status == JobStatus.REVIEWED)
            .where(Job.deleted_at.is_(None))
            .order_by(Job.match_score.desc().nullslast())  # Best matches first
        )
    return list(result.scalars().all())


# ── Save Application Record ─────────────────────────────────────────────────────

async def save_application(
    job: Job,
    status: ApplicationStatus,
    screenshot_path: str | None,
    error_message: str | None,
    session: AsyncSession,
) -> None:
    """
    Create an Application audit record and update the parent Job's status.

    WHY TWO WRITES:
      1. Application row: permanent record of every apply attempt with its outcome.
         Multiple attempts on the same job create multiple rows (retry audit trail).
      2. Job.status UPDATE: keeps the dashboard accurate — APPLIED jobs won't show
         up again in the "to review" queue.

    STATUS MAPPING:
      SUBMITTED → Job.status = APPLIED   (done, don't show in queue again)
      FAILED    → Job.status = FAILED    (needs attention, visible to user)
      PENDING   → Job.status unchanged   (dry run — keep as REVIEWED for real run later)

    NOTE: We use a raw SQL UPDATE for job status (not load-then-set) to be safe
    if multiple workers ever run simultaneously in the future.

    Args:
        job:             The Job that was applied to.
        status:          Outcome of the apply attempt.
        screenshot_path: Path to the Playwright screenshot, or None.
        error_message:   Error description if status=FAILED, else None.
        session:         Active DB session.
    """
    application = Application(
        job_id=job.id,
        status=status,
        screenshot_path=screenshot_path,
        error_message=error_message,
        ats_system="greenhouse",
        # applied_at is only set on real submission — not dry runs or failures
        applied_at=datetime.now(UTC) if status == ApplicationStatus.SUBMITTED else None,
    )
    session.add(application)

    # Update Job.status to reflect the outcome
    if status == ApplicationStatus.SUBMITTED:
        new_job_status = JobStatus.APPLIED
    elif status == ApplicationStatus.FAILED:
        new_job_status = JobStatus.FAILED
    else:
        new_job_status = None  # Dry run (PENDING) — leave job as REVIEWED

    if new_job_status is not None:
        await session.execute(
            update(Job).where(Job.id == job.id).values(status=new_job_status)
        )

    # flush(): write changes to DB within the current transaction,
    # without committing. The transaction is committed by the caller (run()).
    await session.flush()


# ── Form Context Detection ──────────────────────────────────────────────────────

async def _find_form_context(page: Page, timeout_ms: int) -> Page | Frame:
    """
    Return the Page or Frame that contains the Greenhouse application form.

    WHY THIS EXISTS:
      Greenhouse job listings come in two layouts:

      Type A — boards.greenhouse.io/company/jobs/id:
        The form IS the page. #first_name is in the main document.

      Type B — company.com/careers/id?gh_jid=id  (Brex, Instacart, Airbnb, etc.):
        The company's branded career page. The actual Greenhouse form is embedded in
        an <iframe> pointing to job-boards.greenhouse.io/embed/job_app (or
        boards.greenhouse.io/embed/job_app). The main page has no #first_name.

      Both types are extremely common. We detect which we have by:
        1. Waiting 5s for #first_name on the main page (Type A fast path)
        2. Scanning frames for a greenhouse.io URL (Type B — the iframe approach)
        3. Falling back to clicking an Apply button if neither worked

      CONCEPT — Playwright Frames:
        A browser can contain multiple "frames" — the main page plus any <iframe>
        elements. `page.frames` returns all of them. Playwright's Frame object has
        the same fill/click/locator API as Page, so we can use it interchangeably
        once we find the right one.

        The one thing Frame does NOT support is `screenshot()` — that's Page-only.
        We always call page.screenshot() for screenshots, even when filling an iframe.

    Returns:
        The Page or Frame containing #first_name, ready to use for form filling.

    Raises:
        RuntimeError if the form can't be found after all strategies are exhausted.
    """
    # ── Fast path: form is on the main page (Type A) ──────────────────────────
    try:
        await page.wait_for_selector("#first_name", timeout=5_000)
        return page
    except Exception:
        pass

    # ── Check for Greenhouse iframe (Type B) ──────────────────────────────────
    # After page load, any embedded Greenhouse iframe will appear in page.frames.
    # We look for frames whose URL contains "greenhouse.io" and "embed".
    # Both boards.greenhouse.io/embed and job-boards.greenhouse.io/embed are used.
    for frame in page.frames:
        if "greenhouse.io" in frame.url and "embed" in frame.url:
            try:
                await frame.wait_for_selector("#first_name", timeout=10_000)
                logger.debug(
                    "Greenhouse form found in iframe",
                    extra={"agent_name": "apply", "frame_url": frame.url[:80]},
                )
                return frame
            except Exception:
                continue

    # ── Try clicking an Apply button then re-check ────────────────────────────
    # Some pages require a button click before the form/iframe loads.
    _APPLY_SELECTORS = [
        "#apply_button",
        "button[id*='apply']",
        "button:has-text('Apply for this job')",
        "button:has-text('Apply Now')",
        "a:has-text('Apply for this job')",
        "a:has-text('Apply Now')",
    ]
    for selector in _APPLY_SELECTORS:
        try:
            await page.click(selector, timeout=3_000)
            # After clicking, check main page and all frames again
            try:
                await page.wait_for_selector("#first_name", timeout=5_000)
                return page
            except Exception:
                pass
            for frame in page.frames:
                if "greenhouse.io" in frame.url:
                    try:
                        await frame.wait_for_selector("#first_name", timeout=5_000)
                        return frame
                    except Exception:
                        continue
        except Exception:
            continue

    raise RuntimeError(
        f"Could not find the Greenhouse application form on {page.url}. "
        "The page layout is not supported (no #first_name on page or in any iframe)."
    )


# ── Greenhouse Form Automation ──────────────────────────────────────────────────

async def apply_greenhouse(
    job: Job,
    profile: UserProfile,
    page: Page,
    config: ApplyConfig,
) -> tuple[ApplicationStatus, str | None]:
    """
    Navigate to a Greenhouse job posting and fill the application form.

    GREENHOUSE URL TYPES:
      The scraper stores `absolute_url` from Greenhouse's API. Companies can either:
        Type A: boards.greenhouse.io/{slug}/jobs/{id}
                The application form loads immediately. #first_name is on the page.
        Type B: company.com/careers/{id}?gh_jid={id}
                A branded job description page. Must click an "Apply" button first.
                After clicking, Greenhouse typically navigates to a Type A URL.

      We handle both: wait 5s for #first_name to appear (Type A fast path), then
      try Apply button selectors if the form doesn't show (Type B slow path).

    GREENHOUSE FORM STRUCTURE (once on the form):
      Required fields (stable IDs, same across all Greenhouse companies):
        #first_name, #last_name, #email, #phone

      Resume upload:
        input[type='file'] — a hidden file input inside Greenhouse's styled uploader.
        Playwright's set_input_files() can set files on hidden inputs directly.

      Optional fields (vary by company):
        We use wildcard attribute selectors (input[id*='linkedin']) and wrap each
        fill() in a try/except — if the field doesn't exist, we skip it silently.

    FORM FILL ORDER:
      1. Navigate to the URL; detect Type A or B and click Apply if needed
      2. Fill required fields (name, email, phone)
      3. Upload resume via file input
      4. Fill optional fields (linkedin, github, portfolio)
      5. Screenshot the completed form
      6. If not dry_run: click submit → wait for confirmation → screenshot result

    DRY RUN:
      Returns ApplicationStatus.PENDING and the form screenshot path.
      The Job's status stays REVIEWED — run again for real later.

    Args:
        job:     Job to apply to. job.source_url must be the Greenhouse apply URL.
        profile: UserProfile with personal info and resume_path.
        page:    Playwright Page object — real in production, AsyncMock in tests.
        config:  Apply configuration (dry_run flag, timeouts, screenshots_dir).

    Returns:
        (status, screenshot_path)
        SUBMITTED + result screenshot path on success (non-dry-run).
        PENDING + form screenshot path on dry run.

    Raises:
        Any unhandled Playwright exception propagates to run()'s try/except,
        which records it as ApplicationStatus.FAILED.
    """
    screenshots_dir = get_screenshots_dir(config)
    first_name, last_name = split_full_name(profile.full_name)

    logger.info(
        "Navigating to Greenhouse application",
        extra={
            "agent_name": "apply",
            "job_id": str(job.id),
            "company": job.company,
            "url": job.source_url,
        },
    )

    # ── Navigate to the application page ─────────────────────────────────────
    await page.goto(job.source_url, timeout=config.page_timeout_ms)

    # ── Find the form — main page or iframe ───────────────────────────────────
    # _find_form_context() handles both URL types:
    #   Type A (boards.greenhouse.io directly): form is on the main page
    #   Type B (company career page):           form is in a greenhouse.io iframe
    # Returns a Page or Frame — both support fill/click/locator identically.
    ctx = await _find_form_context(page, config.page_timeout_ms)

    logger.info(
        "Form context found",
        extra={
            "agent_name": "apply",
            "job_id": str(job.id),
            "in_iframe": ctx is not page,
        },
    )

    # ── Required fields ───────────────────────────────────────────────────────
    # These IDs are stable across virtually all Greenhouse postings.
    # We use `ctx` (not `page`) so fills work whether the form is in an iframe or not.
    await ctx.fill("#first_name", first_name)
    await ctx.fill("#last_name", last_name)
    await ctx.fill("#email", profile.email)
    await ctx.fill("#phone", profile.phone or "")

    # ── Resume upload ─────────────────────────────────────────────────────────
    # Greenhouse wraps the <input type="file"> inside a styled component.
    # set_input_files() bypasses the UI and sets the file on the hidden input.
    # WHY .first: some forms have multiple file inputs (resume + cover letter).
    try:
        file_input = ctx.locator("input[type='file']").first
        await file_input.set_input_files(profile.resume_path)
    except Exception as e:
        logger.warning(
            "Could not upload resume file — continuing",
            extra={"agent_name": "apply", "job_id": str(job.id), "error": str(e)},
        )

    # ── Optional fields ───────────────────────────────────────────────────────
    # build_optional_field_map() returns only fields where the profile has a value.
    # Wrapped in try/except: skip silently if a field isn't on this company's form.
    for selector, value in build_optional_field_map(profile):
        try:
            await ctx.fill(selector, value)
        except Exception:
            pass  # Field not present on this form — skip silently

    # ── Screenshot: completed form ────────────────────────────────────────────
    # NOTE: screenshot() is a Page method only — Frame doesn't support it.
    # page.screenshot() captures the full page including any iframes, so we get
    # a complete picture regardless of whether the form is in an iframe.
    form_screenshot = str(screenshots_dir / screenshot_filename(job.id, "form"))
    await page.screenshot(path=form_screenshot)

    logger.info(
        "Form filled and screenshotted",
        extra={
            "agent_name": "apply",
            "job_id": str(job.id),
            "dry_run": config.dry_run,
            "screenshot": form_screenshot,
        },
    )

    # ── Dry run: stop here, do not submit ─────────────────────────────────────
    if config.dry_run:
        logger.info(
            "DRY RUN — form filled, skipping submit",
            extra={"agent_name": "apply", "job_id": str(job.id)},
        )
        return ApplicationStatus.PENDING, form_screenshot

    # ── Submit the form ───────────────────────────────────────────────────────
    # Submit button is inside the form context (iframe or main page).
    await ctx.click("input[type='submit'], button[type='submit']")

    # Wait for the submission to complete.
    # "networkidle" waits for all XHR/fetch requests to finish (Greenhouse makes
    # async API calls to record the application after the form is submitted).
    await page.wait_for_load_state("networkidle", timeout=config.page_timeout_ms)

    # ── Screenshot: result / confirmation page ────────────────────────────────
    result_screenshot = str(screenshots_dir / screenshot_filename(job.id, "result"))
    await page.screenshot(path=result_screenshot)

    logger.info(
        "Application submitted successfully",
        extra={
            "agent_name": "apply",
            "job_id": str(job.id),
            "company": job.company,
            "title": job.title,
            "screenshot": result_screenshot,
        },
    )

    return ApplicationStatus.SUBMITTED, result_screenshot


# ── Main Entry Point ─────────────────────────────────────────────────────────────

async def run(
    job_ids: list[uuid.UUID] | None = None,
    dry_run: bool = False,
    config: ApplyConfig | None = None,
    session: AsyncSession | None = None,
) -> ApplyResult:
    """
    Main entry point for the Apply Agent.

    Loads the user profile, fetches reviewed jobs, filters by min_score, then
    applies to each one via Playwright browser automation. One job failing does
    not prevent the others from being attempted.

    FLOW:
      1. Build config from settings (or use injected config from tests)
      2. Load UserProfile — fail fast if missing or incomplete
      3. Fetch jobs with status=REVIEWED (or specific job_ids)
      4. Filter by min_score — count skipped jobs for observability
      5. Launch Playwright browser
      6. For each applicable job: open page → fill form → screenshot → save result
      7. Return ApplyResult with counts

    RESILIENCE:
      Each job is wrapped in its own try/except. A broken Greenhouse form on job #1
      records a FAILED application and moves on to job #2. Errors accumulate in
      result.errors for the caller to inspect or log.

    Args:
        job_ids: Specific jobs to apply to. None = all REVIEWED jobs above min_score.
        dry_run: Fill forms and screenshot but never submit. Overrides config.dry_run.
        config:  Full ApplyConfig. If None, built from environment settings.
        session: DB session injected from tests; created here in production.

    Returns:
        ApplyResult with counts of applied, dry_run, failed, and skipped jobs.
    """
    # Build config from settings if not injected (production path).
    # If config is injected (test path), still respect the dry_run arg override.
    if config is None:
        config = ApplyConfig(
            headless=settings.apply_headless,
            # --dry-run CLI arg / function arg takes precedence over the .env setting
            dry_run=dry_run or settings.apply_dry_run,
            min_score=settings.apply_min_score,
            screenshots_dir=settings.screenshots_dir,
        )
    elif dry_run:
        # If a config was injected but dry_run=True was also passed, honor it.
        # Rebuild with dry_run=True so the rest of the code sees a consistent config.
        config = ApplyConfig(
            headless=config.headless,
            dry_run=True,
            min_score=config.min_score,
            screenshots_dir=config.screenshots_dir,
            page_timeout_ms=config.page_timeout_ms,
        )

    result = ApplyResult()

    logger.info(
        "Apply Agent run starting",
        extra={
            "agent_name": "apply",
            "dry_run": config.dry_run,
            "min_score": config.min_score,
            "job_ids": [str(j) for j in job_ids] if job_ids else "all reviewed",
        },
    )

    # ── Session management ─────────────────────────────────────────────────────
    # Same pattern as resume_match.py:
    #   - Tests inject a session so they control the transaction (rollback after test).
    #   - Production creates a session here and commits when the run completes.
    if session is not None:
        await _run_with_session(job_ids, config, result, session)
        await session.flush()
    else:
        async with AsyncSessionLocal() as session:
            await _run_with_session(job_ids, config, result, session)
            await session.commit()

    logger.info(
        "Apply Agent run complete",
        extra={
            "agent_name": "apply",
            "total_attempted": result.total_attempted,
            "total_applied": result.total_applied,
            "total_dry_run": result.total_dry_run,
            "total_failed": result.total_failed,
            "total_skipped": result.total_skipped,
        },
    )

    return result


async def _run_with_session(
    job_ids: list[uuid.UUID] | None,
    config: ApplyConfig,
    result: ApplyResult,
    session: AsyncSession,
) -> None:
    """
    Inner implementation of the apply loop. Extracted for clean session management.

    CONCEPT — One failure does not stop others:
      Each job is wrapped in its own try/except. If Playwright crashes on job #1
      (e.g. unusual form layout, network timeout), job #2 is still attempted.
      The error is recorded in result.errors and an Application row is created
      with status=FAILED so the user can see what went wrong.
    """

    # ── Load profile (fail fast) ───────────────────────────────────────────────
    try:
        profile = await load_profile(session)
    except RuntimeError as e:
        logger.error(
            "Apply Agent aborted — profile not ready",
            extra={"agent_name": "apply", "error": str(e)},
        )
        result.errors.append(f"Profile error: {e}")
        return

    # ── Fetch reviewed jobs ────────────────────────────────────────────────────
    jobs = await fetch_reviewed_jobs(session, job_ids)

    if not jobs:
        logger.info(
            "No reviewed jobs found — nothing to apply to",
            extra={"agent_name": "apply"},
        )
        return

    # ── Filter by min_score ────────────────────────────────────────────────────
    # Done in Python (not SQL) so we can count skipped jobs for observability.
    applicable_jobs: list[Job] = []
    for job in jobs:
        if job.match_score is None or job.match_score < config.min_score:
            result.total_skipped += 1
            logger.info(
                "Skipping job below min_score threshold",
                extra={
                    "agent_name": "apply",
                    "job_id": str(job.id),
                    "company": job.company,
                    "score": job.match_score,
                    "min_score": config.min_score,
                },
            )
        else:
            applicable_jobs.append(job)

    if not applicable_jobs:
        logger.info(
            "All reviewed jobs below min_score — nothing to apply to",
            extra={"agent_name": "apply", "threshold": config.min_score},
        )
        return

    logger.info(
        "Launching Playwright browser",
        extra={
            "agent_name": "apply",
            "headless": config.headless,
            "jobs_to_attempt": len(applicable_jobs),
        },
    )

    # ── Playwright browser lifecycle ───────────────────────────────────────────
    # async_playwright() is a context manager that starts the Playwright server
    # and shuts it down cleanly when we're done.
    #
    # We launch ONE browser and reuse it for ALL jobs — this is significantly faster
    # than launching a new browser per job (browser startup takes ~1-2 seconds).
    #
    # Each job gets its own page (browser tab) so state from one form doesn't
    # bleed into the next (cookies, form state, JS errors, etc.).
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=config.headless)
        context = await browser.new_context()

        for job in applicable_jobs:
            result.total_attempted += 1
            page = await context.new_page()

            try:
                status, screenshot = await apply_greenhouse(job, profile, page, config)
                await save_application(job, status, screenshot, None, session)

                if status == ApplicationStatus.SUBMITTED:
                    result.total_applied += 1
                elif status == ApplicationStatus.PENDING:
                    # PENDING = dry run (form filled but not submitted)
                    result.total_dry_run += 1

            except Exception as e:
                error_msg = f"{job.company} — {job.title}: {e}"
                result.total_failed += 1
                result.errors.append(error_msg)

                logger.error(
                    "Failed to apply to job",
                    extra={
                        "agent_name": "apply",
                        "job_id": str(job.id),
                        "company": job.company,
                        "title": job.title,
                        "error": str(e),
                    },
                )

                # Record the failure in the DB even if the apply crashed mid-way.
                # WHY separate try/except: if save_application itself fails (e.g. DB error),
                # we don't want that to hide the original apply error.
                try:
                    await save_application(
                        job, ApplicationStatus.FAILED, None, str(e), session
                    )
                except Exception as save_err:
                    logger.error(
                        "Could not save failed application record",
                        extra={"agent_name": "apply", "error": str(save_err)},
                    )

            finally:
                # Always close the page — even if the apply crashed.
                # Leaving pages open leaks memory and can cause flaky behavior
                # in subsequent loop iterations.
                await page.close()

        await context.close()
        await browser.close()


# ── CLI Entry Point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Apply Agent — auto-apply to reviewed Greenhouse jobs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry run — fill forms and screenshot, but never submit (safe on live boards)
  python -m agents.apply --dry-run

  # Apply to all reviewed jobs above the min_score threshold
  python -m agents.apply

  # Apply to specific jobs by UUID
  python -m agents.apply --job-ids abc123 def456
        """,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fill forms and screenshot but never click submit (safe for testing)",
    )
    parser.add_argument(
        "--job-ids",
        nargs="+",
        type=uuid.UUID,
        default=None,
        metavar="UUID",
        help="Specific job UUIDs to apply to. Omit to apply to all reviewed jobs.",
    )

    args = parser.parse_args()

    result = asyncio.run(run(job_ids=args.job_ids, dry_run=args.dry_run))

    print(
        f"\nResult: {result.total_applied} applied, "
        f"{result.total_dry_run} dry-run, "
        f"{result.total_failed} failed, "
        f"{result.total_skipped} skipped "
        f"(from {result.total_attempted} attempted)"
    )
    if result.errors:
        print("\nErrors:")
        for err in result.errors:
            print(f"  - {err}")

    sys.exit(0 if result.total_failed == 0 else 1)
