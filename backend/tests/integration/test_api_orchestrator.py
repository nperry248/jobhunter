"""
tests/integration/test_api_orchestrator.py — API endpoint tests for the Orchestrator routes.

WHAT WE'RE TESTING:
  - POST /run: returns session_id immediately
  - GET /status/{id}: 404 for unknown sessions
  - GET /status/{id}: returns the in-memory running state for known sessions
  - POST /approve/{id}: 404 for unknown sessions
  - POST /approve/{id}: 409 when session is not waiting_for_approval
  - GET /history: returns empty list when no sessions exist
  - GET /history: returns sessions after POST /run is called

CONCEPT — Mocking the orchestrator:
  The agent loop makes real Claude API calls and runs real agents (scraper, scorer).
  We don't want that in tests — it's slow, costs money, and has side effects.
  Instead, we mock `agents.orchestrator.run` so the background task completes
  instantly with a predictable result.

  pytest-mock provides `mocker.patch()` which replaces the real function with a
  MagicMock for the duration of the test, then restores the original.
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from agents.orchestrator_logic import OrchestratorResult
from api.routes.orchestrator import _sessions
from models.orchestrator_session import OrchestratorSession, SessionStatus


@pytest.fixture(autouse=True)
def clear_sessions():
    """
    Clear the in-memory _sessions cache before and after each test.
    WHY: _sessions is module-level state. Without clearing it, one test's
    side effects (a registered session) would pollute the next test.
    """
    _sessions.clear()
    yield
    _sessions.clear()


class TestPostRun:
    async def test_returns_session_id_and_started_status(
        self, client: AsyncClient
    ) -> None:
        """
        POST /run should return a session_id and status="started" immediately.
        The background task hasn't run yet — we just want the booking response.
        """
        response = await client.post(
            "/api/v1/orchestrator/run",
            json={"goal": "Find me good SWE jobs"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "session_id" in data
        assert data["status"] == "started"

    async def test_returns_unique_session_ids(
        self, client: AsyncClient
    ) -> None:
        """Each POST /run should produce a unique session_id."""
        r1 = await client.post("/api/v1/orchestrator/run", json={"goal": "Find jobs"})
        r2 = await client.post("/api/v1/orchestrator/run", json={"goal": "Find jobs"})
        assert r1.json()["session_id"] != r2.json()["session_id"]

    async def test_dry_run_field_accepted(
        self, client: AsyncClient
    ) -> None:
        """POST /run should accept dry_run=True without validation errors."""
        response = await client.post(
            "/api/v1/orchestrator/run",
            json={"goal": "Find jobs", "dry_run": True},
        )
        assert response.status_code == 200


class TestGetStatus:
    async def test_returns_404_for_unknown_session(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """
        GET /status/{unknown_id} should return 404 — not crash or return 200.
        """
        fake_id = str(uuid.uuid4())
        response = await client.get(f"/api/v1/orchestrator/status/{fake_id}")
        assert response.status_code == 404

    async def test_returns_running_state_for_registered_session(
        self, client: AsyncClient
    ) -> None:
        """
        After POST /run, GET /status/{id} should return the session with status="running".

        We mock _run_orchestrator so the background task does NOT complete during the test.
        Without mocking, httpx's test transport executes background tasks before returning,
        so the session would be "complete" by the time we GET /status.
        """
        # Patch _run_orchestrator to a no-op coroutine so it never updates the session state
        async def _noop(*args, **kwargs):
            pass

        with patch("api.routes.orchestrator._run_orchestrator", new=_noop):
            run_response = await client.post(
                "/api/v1/orchestrator/run",
                json={"goal": "Find me jobs"},
            )
            session_id = run_response.json()["session_id"]

            status_response = await client.get(f"/api/v1/orchestrator/status/{session_id}")

        assert status_response.status_code == 200
        data = status_response.json()
        assert data["session_id"] == session_id
        assert data["status"] == "running"
        assert data["goal"] == "Find me jobs"

    async def test_status_response_has_required_fields(
        self, client: AsyncClient
    ) -> None:
        """Status response must include all fields the frontend depends on."""
        async def _noop(*args, **kwargs):
            pass

        with patch("api.routes.orchestrator._run_orchestrator", new=_noop):
            run_response = await client.post(
                "/api/v1/orchestrator/run",
                json={"goal": "Test goal"},
            )
            session_id = run_response.json()["session_id"]
            status_response = await client.get(f"/api/v1/orchestrator/status/{session_id}")

        data = status_response.json()
        assert "session_id" in data
        assert "status" in data
        assert "goal" in data
        assert "steps" in data
        assert "pending_jobs" in data
        assert "token_usage" in data

    async def test_loads_session_from_db_when_not_in_memory(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """
        When a session exists in the DB but not in _sessions (e.g. after restart),
        GET /status/{db_id} should load it from the DB and return 200.
        """
        # Create a DB session record directly
        db_record = OrchestratorSession(
            goal="DB test goal",
            status=SessionStatus.COMPLETE,
            steps=[],
            token_usage=100,
            result_summary="Done",
        )
        db_session.add(db_record)
        await db_session.flush()

        # Query using the real DB UUID (not the API-generated one)
        response = await client.get(f"/api/v1/orchestrator/status/{db_record.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["goal"] == "DB test goal"
        assert data["status"] == "complete"
        assert data["token_usage"] == 100


class TestPostApprove:
    async def test_returns_404_for_unknown_session(
        self, client: AsyncClient
    ) -> None:
        """POST /approve/{unknown_id} should return 404."""
        fake_id = str(uuid.uuid4())
        # Must send a JSON body (even empty {}) — FastAPI validates the request body
        response = await client.post(f"/api/v1/orchestrator/approve/{fake_id}", json={})
        assert response.status_code == 404

    async def test_returns_409_when_session_not_waiting(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """
        POST /approve/{id} should return 409 when the session status is not
        waiting_for_approval (e.g. it's still running or already complete).
        """
        # Create a COMPLETE session in the DB
        db_record = OrchestratorSession(
            goal="Already done",
            status=SessionStatus.COMPLETE,
            steps=[],
            token_usage=50,
        )
        db_session.add(db_record)
        await db_session.flush()

        response = await client.post(f"/api/v1/orchestrator/approve/{db_record.id}", json={})
        assert response.status_code == 409

    async def test_approve_waiting_session_returns_started(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """
        POST /approve/{id} for a waiting_for_approval session should return
        status="started" and trigger the apply background task.
        """
        # Create a waiting session in the DB
        pending_job_id = str(uuid.uuid4())
        db_record = OrchestratorSession(
            goal="Apply to jobs",
            status=SessionStatus.WAITING_FOR_APPROVAL,
            steps=[],
            token_usage=200,
            pending_job_ids=[pending_job_id],
        )
        db_session.add(db_record)
        await db_session.flush()

        # Mock the resume function so it doesn't run real agents
        with patch("api.routes.orchestrator._resume_orchestrator", new_callable=AsyncMock):
            response = await client.post(
                f"/api/v1/orchestrator/approve/{db_record.id}",
                json={},
            )

        assert response.status_code == 200
        assert response.json()["status"] == "started"


class TestGetHistory:
    async def test_returns_empty_list_when_no_sessions(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """GET /history with no sessions in the DB should return an empty list."""
        response = await client.get("/api/v1/orchestrator/history")
        assert response.status_code == 200
        assert response.json() == []

    async def test_returns_sessions_in_reverse_chronological_order(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """
        GET /history should return most recent session first.
        We create two sessions and verify the order.
        """
        s1 = OrchestratorSession(goal="First session", status=SessionStatus.COMPLETE, steps=[])
        s2 = OrchestratorSession(goal="Second session", status=SessionStatus.COMPLETE, steps=[])
        db_session.add(s1)
        db_session.add(s2)
        await db_session.flush()

        response = await client.get("/api/v1/orchestrator/history")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        # Most recent first — s2 was created after s1
        assert data[0]["goal"] == "Second session"
        assert data[1]["goal"] == "First session"

    async def test_history_items_have_required_fields(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """History items must include all fields the frontend summary panel needs."""
        session = OrchestratorSession(
            goal="Test session",
            status=SessionStatus.FAILED,
            steps=[],
            token_usage=42,
            result_summary="Something went wrong",
        )
        db_session.add(session)
        await db_session.flush()

        response = await client.get("/api/v1/orchestrator/history")
        assert response.status_code == 200
        item = response.json()[0]
        assert "session_id" in item
        assert "status" in item
        assert "goal" in item
        assert "token_usage" in item
        assert item["token_usage"] == 42
        assert item["result_summary"] == "Something went wrong"

    async def test_history_respects_limit_and_offset(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """GET /history?limit=1&offset=1 should return the second record only."""
        s1 = OrchestratorSession(goal="Session A", status=SessionStatus.COMPLETE, steps=[])
        s2 = OrchestratorSession(goal="Session B", status=SessionStatus.COMPLETE, steps=[])
        s3 = OrchestratorSession(goal="Session C", status=SessionStatus.COMPLETE, steps=[])
        for s in [s1, s2, s3]:
            db_session.add(s)
        await db_session.flush()

        response = await client.get("/api/v1/orchestrator/history?limit=1&offset=1")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
