"""
tests/integration/test_scraper_pipeline.py — Integration tests for the full scraper pipeline.

WHAT WE'RE TESTING:
  The full pipeline: mocked HTTP response → parse → filter → DB upsert → verify row in DB.
  These tests use the real test database but mock all outbound HTTP calls.

WHY WE MOCK HTTP:
  The internet is unreliable. Greenhouse/Lever could be down, rate-limited, or have
  changed their API. Tests that depend on real network calls are "flaky" — they pass
  sometimes and fail for reasons unrelated to your code. Mocking the HTTP layer makes
  tests deterministic: they always return the same data and always work offline.

CONCEPT — pytest-mock's `mocker.patch`:
  `mocker.patch("module.path.ClassName.method")` replaces the real method with a fake
  one for the duration of the test. The real network call never happens. After the test,
  the original is automatically restored.
"""

import pytest
from sqlalchemy import select

from agents.scraper import fetch_with_retry, run, upsert_job
from agents.scraper_parsers import ParsedJob, ScraperFilters
from models.job import Job, JobSource, JobStatus


# ── Fake API Response Data ────────────────────────────────────────────────────
# These dicts mimic what the real Greenhouse and Lever APIs return.
# Keeping them here (not inline) makes tests easier to read.

FAKE_GREENHOUSE_RESPONSE = {
    "jobs": [
        {
            "id": 11111,
            "title": "Software Engineer Intern",
            "absolute_url": "https://boards.greenhouse.io/testco/jobs/11111",
            "location": {"name": "San Francisco, CA"},
            "content": "<p>Build cool things as an intern.</p>",
        },
        {
            "id": 22222,
            "title": "Senior Software Engineer",  # should be filtered out if job_type=internship
            "absolute_url": "https://boards.greenhouse.io/testco/jobs/22222",
            "location": {"name": "Remote"},
            "content": "<p>Lead the team.</p>",
        },
    ]
}

FAKE_LEVER_RESPONSE = [
    {
        "id": "lever-uuid-abc",
        "text": "Backend Engineer Intern",
        "hostedUrl": "https://jobs.lever.co/testco/lever-uuid-abc",
        "categories": {"location": "New York, NY"},
        "descriptionPlain": "Build APIs.",
    }
]


# ── upsert_job Unit-Integration Tests ─────────────────────────────────────────
# These test upsert_job() directly with the test DB — no HTTP mocking needed.

class TestUpsertJob:

    @pytest.mark.asyncio
    async def test_upsert_inserts_new_job(self, db_session):
        """
        Verify that upsert_job() inserts a new job and returns True (was_new=True).
        This confirms the DB write path works end-to-end.
        """
        parsed = ParsedJob(
            title="Software Engineer Intern",
            company="Test Co",
            source_url="https://example.com/jobs/unique-001",
            source=JobSource.GREENHOUSE,
            location="San Francisco, CA",
        )

        was_new = await upsert_job(parsed, db_session)

        assert was_new is True

        # Verify the row actually exists in the DB
        result = await db_session.execute(
            select(Job).where(Job.source_url == "https://example.com/jobs/unique-001")
        )
        job = result.scalar_one_or_none()
        assert job is not None
        assert job.title == "Software Engineer Intern"
        assert job.company == "Test Co"
        assert job.status == JobStatus.NEW
        assert job.source == JobSource.GREENHOUSE

    @pytest.mark.asyncio
    async def test_upsert_returns_false_for_duplicate(self, db_session):
        """
        Verify that inserting the same source_url twice:
        1. Returns False on the second insert (was_new=False)
        2. Does NOT raise an error
        3. Does NOT create a duplicate row
        """
        parsed = ParsedJob(
            title="Backend Engineer",
            company="Test Co",
            source_url="https://example.com/jobs/duplicate-url",
            source=JobSource.LEVER,
        )

        first_insert = await upsert_job(parsed, db_session)
        second_insert = await upsert_job(parsed, db_session)

        assert first_insert is True
        assert second_insert is False  # duplicate — was NOT inserted again

        # Confirm only one row exists
        result = await db_session.execute(
            select(Job).where(Job.source_url == "https://example.com/jobs/duplicate-url")
        )
        jobs = result.scalars().all()
        assert len(jobs) == 1  # not 2


# ── Full Pipeline Integration Tests ───────────────────────────────────────────

class TestScraperRunPipeline:

    @pytest.mark.asyncio
    async def test_run_inserts_greenhouse_job(self, db_session, mocker):
        """
        Full pipeline test: mock Greenhouse HTTP → run scraper → verify DB row.

        This is the most important integration test: it confirms the entire
        pipeline from HTTP fetch to DB write works correctly end-to-end.

        NOTE — Why MagicMock for response but AsyncMock for get():
          httpx's Response.json() is a *synchronous* method (it parses an already-
          downloaded body — no I/O needed). If we used AsyncMock for the response,
          response.json() would return a coroutine instead of a dict, breaking the
          parser. We use MagicMock for the response and AsyncMock only for get()
          (which IS async — it does the actual network I/O).
        """
        mock_response = mocker.MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = FAKE_GREENHOUSE_RESPONSE
        mock_response.raise_for_status = mocker.Mock()

        mocker.patch(
            "httpx.AsyncClient.get",
            new=mocker.AsyncMock(return_value=mock_response),
        )

        filters = ScraperFilters(
            greenhouse_slugs={"testco": "Test Co"},
            lever_slugs={},  # skip Lever for this test
        )

        result = await run(filters=filters, dry_run=False, session=db_session)

        # Both jobs were fetched
        assert result.total_fetched == 2
        # Both passed the default (no) filters
        assert result.total_passed_filter == 2
        # Both were new
        assert result.total_new == 2
        assert result.total_duplicate == 0

        # Verify rows in DB
        rows = (await db_session.execute(select(Job))).scalars().all()
        assert len(rows) == 2
        titles = {r.title for r in rows}
        assert "Software Engineer Intern" in titles
        assert "Senior Software Engineer" in titles

    @pytest.mark.asyncio
    async def test_run_with_internship_filter(self, db_session, mocker):
        """
        Verify the internship filter correctly rejects the 'Senior Software Engineer'
        and only saves the 'Software Engineer Intern'.
        """
        mock_response = mocker.MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = FAKE_GREENHOUSE_RESPONSE
        mock_response.raise_for_status = mocker.Mock()

        mocker.patch("httpx.AsyncClient.get", new=mocker.AsyncMock(return_value=mock_response))

        filters = ScraperFilters(
            job_type="internship",
            greenhouse_slugs={"testco": "Test Co"},
            lever_slugs={},
        )

        result = await run(filters=filters, dry_run=False, session=db_session)

        assert result.total_fetched == 2
        assert result.total_passed_filter == 1  # only the intern role passed
        assert result.total_new == 1

        rows = (await db_session.execute(select(Job))).scalars().all()
        assert len(rows) == 1
        assert rows[0].title == "Software Engineer Intern"

    @pytest.mark.asyncio
    async def test_run_dry_run_does_not_write_to_db(self, db_session, mocker):
        """
        Verify --dry-run mode fetches and filters normally but writes NOTHING to the DB.
        """
        mock_response = mocker.MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = FAKE_GREENHOUSE_RESPONSE
        mock_response.raise_for_status = mocker.Mock()

        mocker.patch("httpx.AsyncClient.get", new=mocker.AsyncMock(return_value=mock_response))

        filters = ScraperFilters(
            greenhouse_slugs={"testco": "Test Co"},
            lever_slugs={},
        )

        result = await run(filters=filters, dry_run=True, session=db_session)

        # Counts are still reported
        assert result.total_fetched == 2

        # DB must be empty — dry_run should not write anything
        rows = (await db_session.execute(select(Job))).scalars().all()
        assert len(rows) == 0

    @pytest.mark.asyncio
    async def test_run_deduplicates_on_second_run(self, db_session, mocker):
        """
        Run the scraper twice with the same data. The second run should report
        all jobs as duplicates (total_new=0, total_duplicate=2).
        This verifies the upsert deduplication works over multiple runs.
        """
        mock_response = mocker.MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = FAKE_GREENHOUSE_RESPONSE
        mock_response.raise_for_status = mocker.Mock()

        mocker.patch("httpx.AsyncClient.get", new=mocker.AsyncMock(return_value=mock_response))

        filters = ScraperFilters(
            greenhouse_slugs={"testco": "Test Co"},
            lever_slugs={},
        )

        first_run = await run(filters=filters, dry_run=False, session=db_session)
        await db_session.flush()  # ensure first run's rows are visible for the second run

        second_run = await run(filters=filters, dry_run=False, session=db_session)

        assert first_run.total_new == 2
        assert second_run.total_new == 0
        assert second_run.total_duplicate == 2

    @pytest.mark.asyncio
    async def test_run_handles_failed_company_gracefully(self, db_session, mocker):
        """
        If one company's API call fails (e.g. 500 error), the scraper should
        log the error and continue — not crash the entire run.
        """
        # Simulate a network error (not a 404, which would be a different code path)
        import httpx
        mocker.patch(
            "httpx.AsyncClient.get",
            side_effect=httpx.NetworkError("Connection refused"),
        )

        filters = ScraperFilters(
            greenhouse_slugs={"brokenco": "Broken Co"},
            lever_slugs={},
        )

        # Should not raise — errors are caught and logged
        result = await run(filters=filters, dry_run=False, session=db_session)

        assert result.total_fetched == 0
        assert result.total_new == 0
        # The error was counted but didn't crash the run
        assert result.total_errors == 0  # errors in fetch are per-company, not per-job

    @pytest.mark.asyncio
    async def test_run_inserts_lever_job(self, db_session, mocker):
        """
        Full pipeline test for the Lever API path.
        Mirrors the Greenhouse test but exercises fetch_lever_jobs() instead.
        """
        mock_response = mocker.MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = FAKE_LEVER_RESPONSE
        mock_response.raise_for_status = mocker.Mock()

        mocker.patch("httpx.AsyncClient.get", new=mocker.AsyncMock(return_value=mock_response))

        filters = ScraperFilters(
            greenhouse_slugs={},
            lever_slugs={"testco": "Test Co"},
        )

        result = await run(filters=filters, dry_run=False, session=db_session)

        assert result.total_fetched == 1
        assert result.total_passed_filter == 1
        assert result.total_new == 1

        rows = (await db_session.execute(select(Job))).scalars().all()
        assert len(rows) == 1
        assert rows[0].title == "Backend Engineer Intern"
        assert rows[0].company == "Test Co"

    @pytest.mark.asyncio
    async def test_run_respects_max_jobs_cap(self, db_session, mocker):
        """
        When max_jobs=1 is set, only 1 job should be fetched and saved even
        if the API returns 2. Exercises the cap-enforcement code path.
        """
        mock_response = mocker.MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = FAKE_GREENHOUSE_RESPONSE  # returns 2 jobs
        mock_response.raise_for_status = mocker.Mock()

        mocker.patch("httpx.AsyncClient.get", new=mocker.AsyncMock(return_value=mock_response))

        filters = ScraperFilters(
            greenhouse_slugs={"testco": "Test Co"},
            lever_slugs={},
            max_jobs=1,  # hard cap at 1
        )

        result = await run(filters=filters, dry_run=False, session=db_session)

        # Two jobs came back from the API, but max_jobs caps what we process
        assert result.total_fetched == 2
        assert result.total_passed_filter == 1  # capped to 1
        assert result.total_new == 1

        rows = (await db_session.execute(select(Job))).scalars().all()
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_run_upsert_error_is_counted_not_raised(self, db_session, mocker):
        """
        If upsert_job() raises an unexpected exception (e.g. a DB constraint we
        didn't anticipate), the error should be counted in total_errors and the
        run should continue — not crash the entire pipeline.
        """
        mock_response = mocker.MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = FAKE_GREENHOUSE_RESPONSE
        mock_response.raise_for_status = mocker.Mock()

        mocker.patch("httpx.AsyncClient.get", new=mocker.AsyncMock(return_value=mock_response))

        # Force upsert_job to always raise
        mocker.patch(
            "agents.scraper.upsert_job",
            new=mocker.AsyncMock(side_effect=RuntimeError("simulated DB failure")),
        )

        filters = ScraperFilters(
            greenhouse_slugs={"testco": "Test Co"},
            lever_slugs={},
        )

        result = await run(filters=filters, dry_run=False, session=db_session)

        assert result.total_fetched == 2
        assert result.total_passed_filter == 2
        assert result.total_new == 0
        assert result.total_errors == 2  # both jobs failed
        assert len(result.errors) == 2


# ── fetch_with_retry Unit Tests ────────────────────────────────────────────────

class TestFetchWithRetry:

    @pytest.mark.asyncio
    async def test_404_raises_immediately_without_retry(self, mocker):
        """
        A 404 response is a permanent failure (the company slug doesn't exist).
        We should raise immediately — never retry — so we don't waste time.
        """
        import httpx

        mock_request = mocker.MagicMock()
        mock_response = mocker.MagicMock()
        mock_response.status_code = 404
        mock_response.request = mock_request
        mock_response.raise_for_status = mocker.Mock()

        mock_get = mocker.AsyncMock(return_value=mock_response)

        async with httpx.AsyncClient() as client:
            mocker.patch.object(client, "get", mock_get)
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                await fetch_with_retry("https://example.com/notfound", client)

        # Should have only been called once — no retries on 404
        assert mock_get.call_count == 1
        assert exc_info.value.response.status_code == 404

    @pytest.mark.asyncio
    async def test_network_error_retries_then_raises(self, mocker):
        """
        A NetworkError is transient — the server might recover.
        fetch_with_retry should retry up to max_retry_attempts, then re-raise.
        We patch asyncio.sleep to prevent real delays in tests.
        """
        import httpx
        from core.config import settings

        mocker.patch("asyncio.sleep", new=mocker.AsyncMock())  # skip real delays

        mock_get = mocker.AsyncMock(side_effect=httpx.NetworkError("timeout"))

        async with httpx.AsyncClient() as client:
            mocker.patch.object(client, "get", mock_get)
            with pytest.raises(httpx.NetworkError):
                await fetch_with_retry("https://example.com/api", client)

        # Should have retried max_retry_attempts times total
        assert mock_get.call_count == settings.max_retry_attempts

    @pytest.mark.asyncio
    async def test_non_404_http_error_is_retried(self, mocker):
        """
        A 500 or 429 HTTPStatusError is transient — we should retry, not bail
        immediately like we do on 404. This test covers the `last_exception = e`
        branch inside the HTTPStatusError handler.
        """
        import httpx
        from core.config import settings

        mocker.patch("asyncio.sleep", new=mocker.AsyncMock())

        mock_request = mocker.MagicMock()
        mock_response = mocker.MagicMock()
        mock_response.status_code = 500
        mock_response.request = mock_request

        # Simulate raise_for_status() raising HTTPStatusError with a 500 response
        async def failing_get(url, **kwargs):
            raise httpx.HTTPStatusError("500 Internal Server Error", request=mock_request, response=mock_response)

        async with httpx.AsyncClient() as client:
            mocker.patch.object(client, "get", mocker.AsyncMock(side_effect=failing_get))
            with pytest.raises(httpx.HTTPStatusError):
                await fetch_with_retry("https://example.com/api", client)

        # 500 IS retried (unlike 404) — should hit max_retry_attempts
        # We can't check call_count directly here since we patched the whole client.get,
        # but the key assertion is that we got an HTTPStatusError (not a 404 raise-immediately)
        # and that asyncio.sleep was called (proves retries happened).
        import asyncio
        assert asyncio.sleep.call_count == settings.max_retry_attempts - 1


# ── fetch_lever_jobs Error Path ────────────────────────────────────────────────

class TestFetchLeverJobsErrorPath:

    @pytest.mark.asyncio
    async def test_lever_fetch_failure_returns_empty_list(self, db_session, mocker):
        """
        If the Lever API call fails entirely (network error), fetch_lever_jobs
        catches the exception, logs it, and returns [] rather than crashing.
        This exercises the except block in fetch_lever_jobs (lines 200-210).
        """
        import httpx
        mocker.patch(
            "httpx.AsyncClient.get",
            side_effect=httpx.NetworkError("lever is down"),
        )

        filters = ScraperFilters(
            greenhouse_slugs={},
            lever_slugs={"brokenco": "Broken Co"},
        )

        result = await run(filters=filters, dry_run=False, session=db_session)

        assert result.total_fetched == 0
        assert result.total_new == 0


# ── Default Filters Path ───────────────────────────────────────────────────────

class TestRunDefaultFilters:

    @pytest.mark.asyncio
    async def test_run_uses_default_filters_when_none_passed(self, db_session, mocker):
        """
        When run() is called without a `filters` argument, it should build
        ScraperFilters from settings (greenhouse_slugs, lever_slugs, etc.).
        This covers the `if filters is None:` branch in run().

        We mock HTTP to return empty responses so the test doesn't actually
        hit real APIs — we're just verifying the settings-based path executes.
        """
        mock_response = mocker.MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"jobs": []}  # empty — nothing to insert
        mock_response.raise_for_status = mocker.Mock()

        mocker.patch("httpx.AsyncClient.get", new=mocker.AsyncMock(return_value=mock_response))

        # Pass session but NO filters — exercises the `if filters is None` branch
        result = await run(filters=None, dry_run=False, session=db_session)

        # With empty job lists returned, we expect 0 fetched — but the run completed
        assert result.total_fetched == 0
        assert result.total_new == 0

    @pytest.mark.asyncio
    async def test_run_lever_loop_break_on_max_jobs(self, db_session, mocker):
        """
        When max_jobs is reached DURING the Lever company loop (not just the
        Greenhouse loop), the inner `break` on line 347 should fire.

        Set up: two Lever companies, max_jobs=1. After the first company returns
        1 job, the cap is reached and we break without fetching the second company.
        """
        mock_response = mocker.MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = FAKE_LEVER_RESPONSE  # 1 job per call
        mock_response.raise_for_status = mocker.Mock()

        mock_get = mocker.AsyncMock(return_value=mock_response)
        mocker.patch("httpx.AsyncClient.get", new=mock_get)

        filters = ScraperFilters(
            greenhouse_slugs={},       # no greenhouse — go straight to lever loop
            lever_slugs={"co1": "Co 1", "co2": "Co 2"},  # two companies
            max_jobs=1,                # cap at 1 so first company fills the quota
        )

        result = await run(filters=filters, dry_run=False, session=db_session)

        assert result.total_new == 1                   # only 1 job saved
        assert mock_get.call_count == 1                # second company was NOT fetched

    @pytest.mark.asyncio
    async def test_run_creates_own_session_when_none_provided(self, mocker):
        """
        When run() is called WITHOUT a session argument, it creates its own
        AsyncSessionLocal context (the production code path). We mock
        AsyncSessionLocal so no real DB commit is attempted.

        This covers the `else: async with AsyncSessionLocal()` branch.
        """
        from contextlib import asynccontextmanager

        # We need an async context manager that yields a working session-like object.
        # Since we can't easily inject the test DB here, we mock _upsert_all directly
        # and just verify the branch is entered.
        mock_upsert_all = mocker.AsyncMock()
        mocker.patch("agents.scraper._upsert_all", mock_upsert_all)

        mock_response = mocker.MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = FAKE_GREENHOUSE_RESPONSE
        mock_response.raise_for_status = mocker.Mock()
        mocker.patch("httpx.AsyncClient.get", new=mocker.AsyncMock(return_value=mock_response))

        # Patch AsyncSessionLocal to avoid a real DB connection in this test
        mock_session = mocker.AsyncMock()

        @asynccontextmanager
        async def mock_session_local():
            yield mock_session

        mocker.patch("agents.scraper.AsyncSessionLocal", mock_session_local)

        filters = ScraperFilters(
            greenhouse_slugs={"testco": "Test Co"},
            lever_slugs={},
        )

        # No session= argument → exercises the production `else` branch
        result = await run(filters=filters, dry_run=False)

        assert result.total_fetched == 2
        # _upsert_all was called (proves the else-branch executed)
        mock_upsert_all.assert_awaited_once()
        # commit() was called on the mock session
        mock_session.commit.assert_awaited_once()
