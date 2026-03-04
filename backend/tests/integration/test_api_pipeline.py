"""
tests/integration/test_api_pipeline.py — Integration tests for pipeline API endpoints.

ENDPOINTS TESTED:
  POST /api/v1/pipeline/run    — trigger pipeline, returns "started" or 409
  GET  /api/v1/pipeline/status — returns current pipeline state

CONCEPT — Testing BackgroundTasks:
  FastAPI's BackgroundTasks run AFTER the response is sent. In tests using the
  httpx AsyncClient, background tasks execute synchronously before the test
  proceeds. This makes them easy to test — we don't need to wait or poll.

  However, our background task calls the actual agents (scraper + resume_match),
  which make real HTTP and DB calls. We mock both agent run() functions so tests
  run offline and don't modify real data.
"""

import dataclasses
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from api.main import app
from api.routes.pipeline import _state  # import state dict to reset between tests


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_pipeline_state():
    """
    Reset the in-memory pipeline state before each test.

    The state dict is module-level — without this fixture, a test that sets
    running=True would affect the next test.
    """
    _state.update({
        "running": False,
        "started_at": None,
        "finished_at": None,
        "last_result": None,
        "last_error": None,
    })
    yield
    # Reset again after test in case it left state dirty
    _state.update({
        "running": False,
        "started_at": None,
        "finished_at": None,
        "last_result": None,
        "last_error": None,
    })


@dataclasses.dataclass
class FakeScraperResult:
    total_fetched: int = 50
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


# ── GET /api/v1/pipeline/status ───────────────────────────────────────────────

class TestGetPipelineStatus:
    async def test_returns_idle_state_by_default(self, client: AsyncClient):
        """
        GET /status should return running=False with all nulls when no run has happened.
        """
        response = await client.get("/api/v1/pipeline/status")
        assert response.status_code == 200
        data = response.json()
        assert data["running"] is False
        assert data["started_at"] is None
        assert data["finished_at"] is None
        assert data["last_result"] is None
        assert data["last_error"] is None

    async def test_reflects_running_state(self, client: AsyncClient):
        """
        If the state dict says running=True, GET /status should reflect that.
        This simulates checking status mid-run.
        """
        _state["running"] = True
        _state["started_at"] = "2026-01-01T00:00:00+00:00"

        response = await client.get("/api/v1/pipeline/status")
        assert response.status_code == 200
        data = response.json()
        assert data["running"] is True
        assert data["started_at"] == "2026-01-01T00:00:00+00:00"

    async def test_reflects_completed_state(self, client: AsyncClient):
        """GET /status should return last_result after a successful run."""
        _state["running"] = False
        _state["last_result"] = {"scrape": {"total_new": 5}, "score": {"total_scored": 5}}
        _state["finished_at"] = "2026-01-01T01:00:00+00:00"

        response = await client.get("/api/v1/pipeline/status")
        assert response.status_code == 200
        data = response.json()
        assert data["last_result"]["scrape"]["total_new"] == 5

    async def test_reflects_error_state(self, client: AsyncClient):
        """GET /status should return last_error if the last run failed."""
        _state["last_error"] = "Connection refused"

        response = await client.get("/api/v1/pipeline/status")
        assert response.status_code == 200
        assert response.json()["last_error"] == "Connection refused"


# ── POST /api/v1/pipeline/run ─────────────────────────────────────────────────

class TestTriggerPipeline:
    async def test_returns_started_status(self, client: AsyncClient):
        """
        POST /run should return {"status": "started"} when no run is in progress.
        Background task is mocked so we don't actually run the pipeline.
        """
        with patch("api.routes.pipeline._run_pipeline", new_callable=AsyncMock):
            response = await client.post("/api/v1/pipeline/run")

        assert response.status_code == 200
        assert response.json()["status"] == "started"

    async def test_returns_409_when_already_running(self, client: AsyncClient):
        """
        POST /run should return 409 Conflict if a run is already in progress.
        Prevents stacking multiple concurrent pipeline runs.
        """
        _state["running"] = True

        response = await client.post("/api/v1/pipeline/run")
        assert response.status_code == 409
        assert "already running" in response.json()["detail"]

    async def test_pipeline_updates_state_on_success(self, client: AsyncClient):
        """
        After the background task completes, the state dict should reflect
        a successful run with last_result populated and running=False.
        """
        with patch("api.routes.pipeline._run_pipeline") as mock_pipeline:
            # Simulate what the real _run_pipeline does to _state
            async def fake_pipeline(resume_path=None):
                _state["running"] = False
                _state["finished_at"] = "2026-01-01T01:00:00+00:00"
                _state["last_result"] = {
                    "scrape": dataclasses.asdict(FakeScraperResult()),
                    "score": dataclasses.asdict(FakeMatchResult()),
                }

            mock_pipeline.side_effect = fake_pipeline
            await client.post("/api/v1/pipeline/run")

        assert _state["running"] is False
        assert _state["last_result"] is not None
        assert _state["last_result"]["scrape"]["total_new"] == 8

    async def test_pipeline_sets_error_on_failure(self, client: AsyncClient):
        """
        If the pipeline raises an exception, last_error should be set
        and running should return to False.
        """
        with patch("api.routes.pipeline._run_pipeline") as mock_pipeline:
            async def fake_failing_pipeline(resume_path=None):
                _state["running"] = False
                _state["finished_at"] = "2026-01-01T01:00:00+00:00"
                _state["last_error"] = "Scraper network error"

            mock_pipeline.side_effect = fake_failing_pipeline
            await client.post("/api/v1/pipeline/run")

        assert _state["running"] is False
        assert _state["last_error"] == "Scraper network error"

    async def test_can_run_again_after_completion(self, client: AsyncClient):
        """
        After a run completes (running=False), POST /run should accept a new run.
        """
        _state["running"] = False
        _state["last_result"] = {"scrape": {}, "score": {}}

        with patch("api.routes.pipeline._run_pipeline", new_callable=AsyncMock):
            response = await client.post("/api/v1/pipeline/run")

        assert response.status_code == 200
        assert response.json()["status"] == "started"
