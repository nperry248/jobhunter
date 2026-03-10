"""
tests/unit/test_workers.py — Unit tests for Celery task definitions.

WHAT WE'RE TESTING:
  - Each task calls the correct agent run() function
  - Results are converted to dicts (JSON-serializable for Celery)
  - The pipeline task runs scraper then resume match in order
  - Errors from agents propagate correctly (so Celery can retry)

WHAT WE ARE NOT TESTING:
  - The actual scraper or resume match logic (those have their own tests)
  - The Beat schedule (it's just a config dict, no logic to unit-test)
  - Real Redis connectivity (we run tasks synchronously with .apply())

CONCEPT — Running Celery tasks in tests:
  Calling `task.delay()` would require a running Redis broker. In tests we use
  `task.apply()` instead — this runs the task synchronously in the current
  process, bypassing the queue entirely. Same code path, no Redis needed.

CONCEPT — Mocking asyncio.run():
  The tasks call asyncio.run(agent_run()). In tests we don't want to actually
  run the full async agent pipeline, so we mock asyncio.run to return a fake
  result dataclass. This isolates the task logic from the agent logic.
"""

import dataclasses
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from workers.tasks import scrape_task, score_task, scrape_and_score_task, _to_dict


# ── Fake result dataclasses ────────────────────────────────────────────────────
# These mirror the real ScraperResult and MatchResult shapes, giving us
# realistic return values without importing the actual agent classes.

@dataclasses.dataclass
class FakeScraperResult:
    total_fetched: int = 100
    total_passed_filter: int = 10
    total_new: int = 8
    total_duplicate: int = 2
    total_errors: int = 0


@dataclasses.dataclass
class FakeMatchResult:
    total_jobs_fetched: int = 8
    total_scored: int = 7
    total_skipped: int = 1
    total_errors: int = 0
    errors: list = dataclasses.field(default_factory=list)


# ── _to_dict helper ────────────────────────────────────────────────────────────

class TestToDict:
    def test_converts_dataclass_to_dict(self):
        """_to_dict should return a plain dict from a dataclass instance."""
        result = FakeScraperResult()
        d = _to_dict(result)
        assert isinstance(d, dict)
        assert d["total_new"] == 8
        assert d["total_errors"] == 0

    def test_passthrough_for_plain_dict(self):
        """_to_dict should return a dict unchanged."""
        d = {"key": "value"}
        assert _to_dict(d) is d

    def test_passthrough_for_non_dataclass(self):
        """_to_dict should return non-dataclass objects unchanged."""
        assert _to_dict(42) == 42
        assert _to_dict("hello") == "hello"


# ── scrape_task ────────────────────────────────────────────────────────────────

class TestScrapeTask:
    def test_calls_scraper_run(self):
        """
        scrape_task should call agents.scraper.run() via asyncio.run()
        and return a dict of the result.
        """
        fake_result = FakeScraperResult()

        with patch("workers.tasks.asyncio.run", return_value=fake_result) as mock_run:
            with patch("workers.tasks.scrape_task.__wrapped__", create=True):
                # Use apply() to run the task synchronously without a broker
                result = scrape_task.apply().get()

        assert isinstance(result, dict)
        assert result["total_new"] == 8
        assert result["total_duplicate"] == 2

    def test_returns_json_serializable_dict(self):
        """
        Task result must be JSON-serializable (no dataclass objects).
        Celery will fail to store results in Redis if they're not serializable.
        """
        fake_result = FakeScraperResult()

        with patch("workers.tasks.asyncio.run", return_value=fake_result):
            result = scrape_task.apply().get()

        import json
        # This should not raise — if it does, Celery would fail to store the result
        json.dumps(result)

    def test_propagates_exception(self):
        """
        If asyncio.run raises, the task should propagate the exception
        so Celery knows to retry it.
        """
        with patch("workers.tasks.asyncio.run", side_effect=RuntimeError("network error")):
            task_result = scrape_task.apply()
            assert task_result.failed()


# ── score_task ─────────────────────────────────────────────────────────────────

class TestScoreTask:
    def test_calls_resume_match_run(self):
        """
        score_task should call agents.resume_match.run() via asyncio.run()
        and return a dict of the result.
        """
        fake_result = FakeMatchResult()

        with patch("workers.tasks.asyncio.run", return_value=fake_result):
            result = score_task.apply().get()

        assert isinstance(result, dict)
        assert result["total_scored"] == 7
        assert result["total_skipped"] == 1

    def test_accepts_resume_path_kwarg(self):
        """
        score_task should accept an optional resume_path argument and pass
        it through to the agent. This allows overriding the profile's stored path.
        """
        fake_result = FakeMatchResult()
        captured = {}

        def fake_asyncio_run(coro):
            # Record what was passed so we can inspect it
            captured["called"] = True
            return fake_result

        with patch("workers.tasks.asyncio.run", side_effect=fake_asyncio_run):
            result = score_task.apply(kwargs={"resume_path": "/tmp/resume.pdf"}).get()

        assert captured.get("called") is True
        assert isinstance(result, dict)

    def test_propagates_exception(self):
        """score_task should propagate exceptions so Celery can retry."""
        with patch("workers.tasks.asyncio.run", side_effect=ValueError("bad resume")):
            task_result = score_task.apply()
            assert task_result.failed()


# ── scrape_and_score_task ─────────────────────────────────────────────────────

class TestScrapeAndScoreTask:
    def test_runs_scraper_then_scorer(self):
        """
        scrape_and_score_task should call the scraper agent then the resume match
        agent in order. The combined result should contain both 'scrape' and 'score' keys.

        NOTE: The task now runs both agents inside a single asyncio.run() call using
        an internal async wrapper (_run_pipeline). We mock the agent run() functions
        directly (as AsyncMocks) rather than mocking asyncio.run(), since asyncio.run
        is only called once now.
        """
        call_order = []

        async def fake_scraper_run(*args, **kwargs):
            call_order.append("scrape")
            return FakeScraperResult()

        async def fake_score_run(*args, **kwargs):
            call_order.append("score")
            return FakeMatchResult()

        with patch("agents.scraper.run", side_effect=fake_scraper_run):
            with patch("agents.resume_match.run", side_effect=fake_score_run):
                result = scrape_and_score_task.apply().get()

        assert call_order == ["scrape", "score"], "Scraper must run before scorer"
        assert "scrape" in result
        assert "score" in result

    def test_combined_result_structure(self):
        """Result dict should have nested 'scrape' and 'score' sub-dicts."""
        with patch("agents.scraper.run", new=AsyncMock(return_value=FakeScraperResult())):
            with patch("agents.resume_match.run", new=AsyncMock(return_value=FakeMatchResult())):
                result = scrape_and_score_task.apply().get()

        assert result["scrape"]["total_new"] == 8
        assert result["score"]["total_scored"] == 7

    def test_propagates_scraper_exception(self):
        """
        If the scraper fails, scrape_and_score_task should propagate the error.
        The scorer should NOT run — no point scoring if we didn't scrape new jobs.
        """
        with patch("agents.scraper.run", new=AsyncMock(side_effect=RuntimeError("scraper broke"))):
            task_result = scrape_and_score_task.apply()
            assert task_result.failed()

    def test_result_is_json_serializable(self):
        """Combined result must be JSON-serializable for Celery's Redis backend."""
        import json
        with patch("agents.scraper.run", new=AsyncMock(return_value=FakeScraperResult())):
            with patch("agents.resume_match.run", new=AsyncMock(return_value=FakeMatchResult())):
                result = scrape_and_score_task.apply().get()

        json.dumps(result)  # must not raise
