"""
tests/integration/test_api_health.py — Integration tests for the /health endpoint.

WHAT WE'RE TESTING:
  - The /health endpoint returns HTTP 200
  - The response body has the expected shape
  - The root / endpoint returns HTTP 200

WHY THESE ARE INTEGRATION TESTS (not unit tests):
  These tests exercise the full request/response cycle:
  Request → CORS Middleware → Route Handler → Response
  Multiple components work together: FastAPI + our route + the httpx client.
  Unit tests would test a function in isolation; here we test the whole stack.

CONCEPT — httpx AsyncClient:
  We use httpx instead of the standard `requests` library because our FastAPI
  app is async. `requests` is synchronous and would block the event loop.
  `httpx` supports async/await natively.
"""

import pytest


@pytest.mark.asyncio
class TestHealthEndpoint:
    """Tests for GET /health."""

    async def test_health_returns_200(self, client):
        """
        The /health endpoint must return HTTP 200 when the server is running.
        This is the most fundamental test: if this fails, nothing else will work.
        """
        response = await client.get("/health")
        assert response.status_code == 200

    async def test_health_response_shape(self, client):
        """
        Verify the /health response contains the expected fields.
        We check for `status`, `service`, and `version` — these are what
        load balancers and monitoring tools look for.
        """
        response = await client.get("/health")
        data = response.json()

        assert "status" in data
        assert data["status"] == "ok"
        assert "service" in data
        assert "version" in data

    async def test_health_content_type_is_json(self, client):
        """
        API responses must be JSON (not HTML, not plain text).
        FastAPI does this by default, but it's worth verifying explicitly.
        """
        response = await client.get("/health")
        assert "application/json" in response.headers["content-type"]


@pytest.mark.asyncio
class TestRootEndpoint:
    """Tests for GET /."""

    async def test_root_returns_200(self, client):
        """
        The root endpoint should return 200 and a helpful message.
        This confirms the app is running and routing works at all.
        """
        response = await client.get("/")
        assert response.status_code == 200

    async def test_root_contains_docs_link(self, client):
        """
        The root response should point users to /docs (the Swagger UI).
        This is a developer-experience test — new developers need to find the docs.
        """
        response = await client.get("/")
        data = response.json()
        assert "docs" in data
