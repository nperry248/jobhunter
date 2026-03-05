"""
tests/integration/test_apply_pipeline.py — Integration tests for the Apply Agent.

MOCKING STRATEGY:
  The Apply Agent uses Playwright (real Chromium browser automation). Launching a
  real browser in tests would be slow (~2-5 seconds per test), fragile (requires
  network access and a live job board), and impossible in CI without a display server.

  Instead we mock at two levels:

  Level 1 — Direct apply_greenhouse() tests (test_dry_run_fills_form_does_not_submit,
             test_non_dry_run_clicks_submit):
    Pass an AsyncMock Page object directly. Verifies form-filling logic without a browser.

  Level 2 — Full run() tests (everything else):
    Mock both async_playwright (so no real browser launches) AND apply_greenhouse
    (so no real form is filled). Verifies orchestration: DB reads, score filtering,
    Application record creation, Job status updates.

CONCEPT — AsyncMock:
  Playwright calls are all async (they're coroutines — you have to `await` them).
  Regular `MagicMock` returns plain values synchronously and can't be awaited.
  `AsyncMock` returns awaitables, so `await mock_page.fill(...)` works correctly.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from agents.apply import apply_greenhouse, run
from agents.apply_logic import ApplyConfig
from models.application import Application, ApplicationStatus
from models.job import Job, JobSource, JobStatus
from models.user_profile import UserProfile


# ── Test Helpers ───────────────────────────────────────────────────────────────

def _make_profile(**kwargs) -> UserProfile:
    """Create a UserProfile with sensible defaults for apply tests."""
    defaults = {
        "full_name": "Nick Perry",
        "email": "nick@example.com",
        "phone": "555-0001",
        "resume_path": "/tmp/fake_resume.pdf",
        "linkedin_url": "https://linkedin.com/in/nickperry",
    }
    defaults.update(kwargs)
    return UserProfile(**defaults)


def _make_reviewed_job(**kwargs) -> Job:
    """Create a REVIEWED job ready for the Apply Agent."""
    defaults = {
        "title": "Software Engineer",
        "company": "Acme Corp",
        "source_url": f"https://boards.greenhouse.io/acme/jobs/{uuid.uuid4()}",
        "source": JobSource.GREENHOUSE,
        "status": JobStatus.REVIEWED,
        "match_score": 85.0,
    }
    defaults.update(kwargs)
    return Job(**defaults)


def _make_mock_page() -> AsyncMock:
    """
    Build a mock Playwright Page with all methods needed by apply_greenhouse().

    WHY SEPARATE SYNC vs ASYNC MOCKS:
      In Playwright's API, some methods are sync and some are async:
        - page.locator()           → SYNC  (returns a Locator immediately, no await)
        - locator.first            → SYNC  (property, returns a Locator)
        - locator.set_input_files  → ASYNC (must be awaited)
        - page.fill, goto, click   → ASYNC (must be awaited)

      If we use AsyncMock() for everything, calling page.locator() returns a
      coroutine — then accessing .first on a coroutine fails with AttributeError,
      which gets swallowed by apply_greenhouse()'s except clause.

      We use MagicMock for sync methods so they return values directly,
      and AsyncMock for async methods so they can be awaited correctly.
    """
    mock_page = AsyncMock()

    # page.locator() is sync in Playwright — override the default AsyncMock behavior.
    mock_locator = MagicMock()
    mock_locator.first = MagicMock()
    # set_input_files IS async — keep it as AsyncMock
    mock_locator.first.set_input_files = AsyncMock()
    mock_page.locator = MagicMock(return_value=mock_locator)

    return mock_page


def _mock_playwright(mocker) -> AsyncMock:
    """
    Patch agents.apply.async_playwright so no real browser is launched.

    Returns the mock page object so tests can inspect what was called on it.

    HOW THE PLAYWRIGHT CONTEXT MANAGER CHAIN WORKS:
      async with async_playwright() as pw:         # context manager → __aenter__ returns pw
          browser = await pw.chromium.launch(...)  # pw is a playwright object
          context = await browser.new_context()
          page = await context.new_page()          # this is what apply_greenhouse uses

    We mock every link in this chain so the code can run without a real browser.
    """
    mock_page = _make_mock_page()
    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)
    mock_browser = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_pw = AsyncMock()
    mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

    # async_playwright() returns a context manager — mock __aenter__ and __aexit__
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_cm.__aexit__ = AsyncMock(return_value=None)

    mocker.patch("agents.apply.async_playwright", return_value=mock_cm)
    return mock_page


# ── Tests: apply_greenhouse() directly ────────────────────────────────────────
# These tests bypass run() and call apply_greenhouse() with a mock page directly.
# Purpose: verify form-filling logic in isolation.

@pytest.mark.asyncio
async def test_dry_run_fills_form_does_not_submit(tmp_path):
    """
    In dry_run mode:
      - All required fields are filled (first name, last name, email, phone)
      - One screenshot is taken (the completed form)
      - Submit is NEVER clicked

    IMPORTANCE OF THIS TEST:
      This is the safety test for the dry_run guarantee. If this fails, we might
      accidentally submit real applications when dry_run=True is set.
    """
    profile = _make_profile()
    job = _make_reviewed_job()
    mock_page = _make_mock_page()

    config = ApplyConfig(dry_run=True, screenshots_dir=str(tmp_path))

    status, screenshot = await apply_greenhouse(job, profile, mock_page, config)

    # ── Verify required fields were filled ────────────────────────────────────
    mock_page.fill.assert_any_call("#first_name", "Nick")
    mock_page.fill.assert_any_call("#last_name", "Perry")
    mock_page.fill.assert_any_call("#email", "nick@example.com")
    mock_page.fill.assert_any_call("#phone", "555-0001")

    # ── Verify exactly ONE screenshot (form only — no result page) ────────────
    mock_page.screenshot.assert_called_once()

    # ── Verify submit was NEVER clicked ───────────────────────────────────────
    mock_page.click.assert_not_called()

    # ── Verify return value ───────────────────────────────────────────────────
    assert status == ApplicationStatus.PENDING  # PENDING = filled but not submitted


@pytest.mark.asyncio
async def test_non_dry_run_clicks_submit_and_takes_two_screenshots(tmp_path):
    """
    In non-dry-run mode:
      - Submit button IS clicked
      - Two screenshots are taken (form + result page)
      - Status is SUBMITTED
    """
    profile = _make_profile()
    job = _make_reviewed_job()
    mock_page = _make_mock_page()

    config = ApplyConfig(dry_run=False, screenshots_dir=str(tmp_path))

    status, screenshot = await apply_greenhouse(job, profile, mock_page, config)

    # Submit SHOULD have been clicked exactly once
    mock_page.click.assert_called_once()

    # Two screenshots: one for the form, one for the result page
    assert mock_page.screenshot.call_count == 2

    assert status == ApplicationStatus.SUBMITTED


@pytest.mark.asyncio
async def test_optional_linkedin_field_filled(tmp_path):
    """
    When the profile has a linkedin_url, apply_greenhouse() should attempt to
    fill an input matching 'linkedin' in its id attribute.
    """
    profile = _make_profile(linkedin_url="https://linkedin.com/in/nick")
    job = _make_reviewed_job()
    mock_page = _make_mock_page()

    config = ApplyConfig(dry_run=True, screenshots_dir=str(tmp_path))
    await apply_greenhouse(job, profile, mock_page, config)

    # Verify a fill call was made with the linkedin selector
    all_calls = [call.args[0] for call in mock_page.fill.call_args_list]
    assert any("linkedin" in sel for sel in all_calls)


# ── Tests: run() full orchestration ───────────────────────────────────────────
# These tests call run() end-to-end, mocking the Playwright browser.

@pytest.mark.asyncio
async def test_run_fails_fast_if_no_profile(db_session):
    """
    If there is no UserProfile in the DB, run() records an error and returns
    without attempting any applications (and without launching a browser).

    WHY TEST THIS:
      The agent must fail gracefully with a clear error — not crash with an
      unhandled exception or silently skip all jobs without explanation.
    """
    # No profile inserted — DB is empty
    config = ApplyConfig(dry_run=True)
    result = await run(config=config, session=db_session)

    # Error recorded, nothing attempted
    assert len(result.errors) == 1
    assert "profile" in result.errors[0].lower() or "UserProfile" in result.errors[0]
    assert result.total_attempted == 0


@pytest.mark.asyncio
async def test_run_skips_jobs_below_min_score(db_session):
    """
    Jobs with match_score below min_score should be counted as skipped,
    not attempted. No browser is launched.

    SCENARIO: job has score=50.0, min_score=70.0 → should be skipped.
    """
    profile = _make_profile()
    db_session.add(profile)

    job = _make_reviewed_job(match_score=50.0)
    db_session.add(job)
    await db_session.flush()

    config = ApplyConfig(min_score=70.0, dry_run=True)
    result = await run(config=config, session=db_session)

    assert result.total_skipped == 1
    assert result.total_attempted == 0


@pytest.mark.asyncio
async def test_run_saves_application_record_on_success(db_session, mocker):
    """
    On a successful application:
      - An Application row is created with status=SUBMITTED
      - Job.status is updated to APPLIED
      - result.total_applied == 1
    """
    profile = _make_profile()
    db_session.add(profile)

    job = _make_reviewed_job(match_score=85.0)
    db_session.add(job)
    await db_session.flush()

    job_id = job.id

    _mock_playwright(mocker)
    mocker.patch(
        "agents.apply.apply_greenhouse",
        new=AsyncMock(return_value=(ApplicationStatus.SUBMITTED, "/tmp/screenshot.png")),
    )

    config = ApplyConfig(min_score=70.0, dry_run=False)
    result = await run(config=config, session=db_session)

    # ── Verify run result ─────────────────────────────────────────────────────
    assert result.total_applied == 1
    assert result.total_failed == 0
    assert result.total_dry_run == 0

    # ── Verify Application row in DB ──────────────────────────────────────────
    app_result = await db_session.execute(
        select(Application).where(Application.job_id == job_id)
    )
    application = app_result.scalar_one_or_none()
    assert application is not None
    assert application.status == ApplicationStatus.SUBMITTED
    assert application.screenshot_path == "/tmp/screenshot.png"
    assert application.ats_system == "greenhouse"
    assert application.applied_at is not None  # Timestamp set on submission

    # ── Verify Job.status updated to APPLIED ─────────────────────────────────
    job_result = await db_session.execute(select(Job).where(Job.id == job_id))
    updated_job = job_result.scalar_one()
    assert updated_job.status == JobStatus.APPLIED


@pytest.mark.asyncio
async def test_one_failure_does_not_stop_others(db_session, mocker):
    """
    If the first job raises an exception during apply_greenhouse(), the second
    job is still attempted and applied successfully.

    This is the MOST IMPORTANT resilience test. A broken Greenhouse form on
    one job must never prevent the rest of the queue from being processed.
    """
    profile = _make_profile()
    db_session.add(profile)

    job1 = _make_reviewed_job(
        match_score=85.0,
        source_url="https://boards.greenhouse.io/acme/jobs/1",
    )
    job2 = _make_reviewed_job(
        match_score=80.0,
        source_url="https://boards.greenhouse.io/acme/jobs/2",
    )
    db_session.add(job1)
    db_session.add(job2)
    await db_session.flush()

    _mock_playwright(mocker)

    # First call raises, second call returns success
    mocker.patch(
        "agents.apply.apply_greenhouse",
        new=AsyncMock(
            side_effect=[
                Exception("Playwright timeout — form not found"),
                (ApplicationStatus.SUBMITTED, "/tmp/job2_screenshot.png"),
            ]
        ),
    )

    config = ApplyConfig(min_score=70.0, dry_run=False)
    result = await run(config=config, session=db_session)

    # ── Verify counts ─────────────────────────────────────────────────────────
    assert result.total_applied == 1
    assert result.total_failed == 1
    assert len(result.errors) == 1

    # ── Verify both Application rows exist ────────────────────────────────────
    apps_result = await db_session.execute(select(Application))
    all_apps = list(apps_result.scalars().all())
    assert len(all_apps) == 2

    statuses = {app.status for app in all_apps}
    assert ApplicationStatus.FAILED in statuses
    assert ApplicationStatus.SUBMITTED in statuses


@pytest.mark.asyncio
async def test_dry_run_does_not_change_job_status(db_session, mocker):
    """
    In dry_run mode, Job.status must remain REVIEWED after the run.

    WHY: Dry runs are meant to be safe and reversible. If a dry run changed the
    job to APPLIED, you'd never be able to actually apply to it for real later.
    """
    profile = _make_profile()
    db_session.add(profile)

    job = _make_reviewed_job(match_score=85.0)
    db_session.add(job)
    await db_session.flush()

    job_id = job.id

    _mock_playwright(mocker)
    # apply_greenhouse returns PENDING to signal dry run
    mocker.patch(
        "agents.apply.apply_greenhouse",
        new=AsyncMock(return_value=(ApplicationStatus.PENDING, "/tmp/form.png")),
    )

    config = ApplyConfig(min_score=70.0, dry_run=True)
    result = await run(config=config, session=db_session)

    assert result.total_dry_run == 1
    assert result.total_applied == 0

    # Job should still be REVIEWED — not APPLIED
    job_result = await db_session.execute(select(Job).where(Job.id == job_id))
    updated_job = job_result.scalar_one()
    assert updated_job.status == JobStatus.REVIEWED


@pytest.mark.asyncio
async def test_run_records_failure_application_on_exception(db_session, mocker):
    """
    When apply_greenhouse() raises an exception, a FAILED Application row
    is created and Job.status is updated to FAILED.

    This gives the user visibility into what went wrong without requiring
    them to dig through logs.
    """
    profile = _make_profile()
    db_session.add(profile)

    job = _make_reviewed_job(match_score=85.0)
    db_session.add(job)
    await db_session.flush()

    job_id = job.id

    _mock_playwright(mocker)
    mocker.patch(
        "agents.apply.apply_greenhouse",
        new=AsyncMock(side_effect=Exception("Unexpected form layout")),
    )

    config = ApplyConfig(min_score=70.0, dry_run=False)
    result = await run(config=config, session=db_session)

    assert result.total_failed == 1
    assert result.total_applied == 0

    # Verify FAILED Application row exists
    app_result = await db_session.execute(
        select(Application).where(Application.job_id == job_id)
    )
    application = app_result.scalar_one_or_none()
    assert application is not None
    assert application.status == ApplicationStatus.FAILED
    assert "Unexpected form layout" in (application.error_message or "")

    # Verify Job.status updated to FAILED
    job_result = await db_session.execute(select(Job).where(Job.id == job_id))
    updated_job = job_result.scalar_one()
    assert updated_job.status == JobStatus.FAILED
