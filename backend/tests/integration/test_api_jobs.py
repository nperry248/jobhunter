"""
tests/integration/test_api_jobs.py — API integration tests for /api/v1/jobs.

WHAT WE'RE TESTING:
  - GET /api/v1/jobs: pagination, filtering, ordering, edge cases
  - PATCH /api/v1/jobs/{id}: happy path, 404, invalid status, invalid UUID

WHY THESE ARE INTEGRATION TESTS:
  These tests make real HTTP requests to the FastAPI app (via httpx AsyncClient)
  and read/write real rows in the test database. We're testing the full stack:
    HTTP request → route handler → DB query → JSON response

  This catches bugs that unit tests miss: wrong SQL, missing index, incorrect
  response schema, dependency injection issues, etc.

CONCEPT — httpx AsyncClient:
  The `client` fixture (from conftest.py) creates an httpx AsyncClient that talks
  directly to the FastAPI ASGI app (no real TCP connection). This means tests run
  instantly with no port conflicts, and we can run hundreds of them in CI.
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from models.job import Job, JobSource, JobStatus


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_job(
    title: str = "SWE Intern",
    company: str = "Acme",
    status: JobStatus = JobStatus.NEW,
    match_score: float | None = None,
    description: str | None = "A great job opportunity",
    source: JobSource = JobSource.GREENHOUSE,
) -> Job:
    """Create a Job with sensible defaults for testing."""
    return Job(
        title=title,
        company=company,
        source_url=f"https://example.com/jobs/{uuid.uuid4()}",
        description=description,
        status=status,
        match_score=match_score,
        source=source,
    )


# ══════════════════════════════════════════════════════════════════════════════
# TestListJobsEndpoint — GET /api/v1/jobs
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestListJobsEndpoint:
    """Tests for GET /api/v1/jobs."""

    async def test_returns_200_with_empty_db(self, client: AsyncClient) -> None:
        """With no jobs in the DB, the endpoint returns 200 with an empty list."""
        response = await client.get("/api/v1/jobs")
        assert response.status_code == 200
        data = response.json()
        assert data["jobs"] == []
        assert data["total"] == 0

    async def test_returns_jobs_in_response(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A job in the DB should appear in the GET /jobs response."""
        job = make_job(title="Backend Engineer", company="Stripe")
        db_session.add(job)
        await db_session.flush()

        response = await client.get("/api/v1/jobs")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert len(data["jobs"]) == 1
        assert data["jobs"][0]["title"] == "Backend Engineer"
        assert data["jobs"][0]["company"] == "Stripe"

    async def test_response_envelope_shape(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Response must include jobs, total, limit, and offset fields."""
        db_session.add(make_job())
        await db_session.flush()

        response = await client.get("/api/v1/jobs")
        data = response.json()
        assert "jobs" in data
        assert "total" in data
        assert "limit" in data
        assert "offset" in data

    async def test_job_object_has_required_fields(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Each job in the response must have all required fields."""
        job = make_job(match_score=85.0)
        db_session.add(job)
        await db_session.flush()

        response = await client.get("/api/v1/jobs")
        job_data = response.json()["jobs"][0]

        assert "id" in job_data
        assert "title" in job_data
        assert "company" in job_data
        assert "source_url" in job_data
        assert "status" in job_data
        assert "match_score" in job_data
        assert "match_reasoning" in job_data
        assert "source" in job_data

    async def test_pagination_limit(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """
        limit=2 with 5 jobs in DB should return exactly 2 jobs in `jobs`
        but `total` should still reflect all 5.
        """
        for i in range(5):
            db_session.add(make_job(title=f"Job {i}"))
        await db_session.flush()

        response = await client.get("/api/v1/jobs?limit=2")
        data = response.json()
        assert len(data["jobs"]) == 2
        assert data["total"] == 5
        assert data["limit"] == 2

    async def test_pagination_offset(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """offset=3 with 5 jobs should return 2 jobs (jobs 4 and 5)."""
        for i in range(5):
            db_session.add(make_job(title=f"Job {i}", match_score=float(i * 10)))
        await db_session.flush()

        response = await client.get("/api/v1/jobs?offset=3")
        data = response.json()
        assert len(data["jobs"]) == 2
        assert data["offset"] == 3
        assert data["total"] == 5

    async def test_ordered_by_match_score_desc(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Jobs should be ordered by match_score DESC so best matches appear first."""
        db_session.add(make_job(title="Low Score", match_score=30.0))
        db_session.add(make_job(title="High Score", match_score=95.0))
        db_session.add(make_job(title="Mid Score", match_score=60.0))
        await db_session.flush()

        response = await client.get("/api/v1/jobs")
        jobs = response.json()["jobs"]
        scores = [j["match_score"] for j in jobs if j["match_score"] is not None]
        assert scores == sorted(scores, reverse=True)

    async def test_null_scores_appear_last(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Jobs with null match_score (unscored) should appear AFTER scored jobs."""
        db_session.add(make_job(title="Scored Job", match_score=70.0))
        db_session.add(make_job(title="Unscored Job", match_score=None))
        await db_session.flush()

        response = await client.get("/api/v1/jobs")
        jobs = response.json()["jobs"]
        assert jobs[0]["title"] == "Scored Job"
        assert jobs[1]["title"] == "Unscored Job"

    async def test_filter_by_status(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """?status=scored should return only SCORED jobs."""
        db_session.add(make_job(title="New Job", status=JobStatus.NEW))
        db_session.add(make_job(title="Scored Job", status=JobStatus.SCORED, match_score=80.0))
        await db_session.flush()

        response = await client.get("/api/v1/jobs?status=scored")
        data = response.json()
        assert data["total"] == 1
        assert data["jobs"][0]["title"] == "Scored Job"
        assert data["jobs"][0]["status"] == "scored"

    async def test_filter_by_invalid_status_returns_400(self, client: AsyncClient) -> None:
        """?status=flying is not a valid status — should return 400 Bad Request."""
        response = await client.get("/api/v1/jobs?status=flying")
        assert response.status_code == 400
        assert "Invalid status" in response.json()["detail"]

    async def test_filter_by_company_partial_match(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """?company=air should match 'Airbnb' (case-insensitive substring match)."""
        db_session.add(make_job(company="Airbnb"))
        db_session.add(make_job(company="Stripe"))
        await db_session.flush()

        response = await client.get("/api/v1/jobs?company=air")
        data = response.json()
        assert data["total"] == 1
        assert data["jobs"][0]["company"] == "Airbnb"

    async def test_filter_by_company_case_insensitive(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Company filter should be case-insensitive."""
        db_session.add(make_job(company="Figma"))
        await db_session.flush()

        response = await client.get("/api/v1/jobs?company=FIGMA")
        data = response.json()
        assert data["total"] == 1

    async def test_filter_by_min_score(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """?min_score=70 should only return jobs with match_score >= 70."""
        db_session.add(make_job(title="Low", match_score=40.0))
        db_session.add(make_job(title="High", match_score=85.0))
        db_session.add(make_job(title="Unscored", match_score=None))
        await db_session.flush()

        response = await client.get("/api/v1/jobs?min_score=70")
        data = response.json()
        assert data["total"] == 1
        assert data["jobs"][0]["title"] == "High"

    async def test_combined_filters(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Multiple filters should be AND-combined."""
        db_session.add(make_job(company="Airbnb", status=JobStatus.SCORED, match_score=90.0))
        db_session.add(make_job(company="Airbnb", status=JobStatus.NEW, match_score=None))
        db_session.add(make_job(company="Stripe", status=JobStatus.SCORED, match_score=88.0))
        await db_session.flush()

        response = await client.get("/api/v1/jobs?company=airbnb&status=scored")
        data = response.json()
        assert data["total"] == 1
        assert data["jobs"][0]["company"] == "Airbnb"
        assert data["jobs"][0]["status"] == "scored"

    async def test_excludes_soft_deleted_jobs(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Soft-deleted jobs (deleted_at is set) must never appear in API responses."""
        from datetime import datetime, timezone

        active_job = make_job(title="Active")
        deleted_job = make_job(title="Deleted")
        deleted_job.deleted_at = datetime.now(timezone.utc)
        db_session.add_all([active_job, deleted_job])
        await db_session.flush()

        response = await client.get("/api/v1/jobs")
        data = response.json()
        titles = [j["title"] for j in data["jobs"]]
        assert "Active" in titles
        assert "Deleted" not in titles

    async def test_default_limit_is_20(self, client: AsyncClient, db_session: AsyncSession) -> None:
        """When no limit is specified, the default should be 20."""
        response = await client.get("/api/v1/jobs")
        assert response.json()["limit"] == 20

    async def test_limit_0_returns_400(self, client: AsyncClient) -> None:
        """limit=0 is invalid (must be >= 1). FastAPI should return 422."""
        response = await client.get("/api/v1/jobs?limit=0")
        assert response.status_code == 422

    async def test_limit_exceeding_200_returns_422(self, client: AsyncClient) -> None:
        """limit=201 exceeds the max of 200. FastAPI should return 422."""
        response = await client.get("/api/v1/jobs?limit=201")
        assert response.status_code == 422

    async def test_negative_offset_returns_422(self, client: AsyncClient) -> None:
        """Negative offsets are invalid. FastAPI should return 422."""
        response = await client.get("/api/v1/jobs?offset=-1")
        assert response.status_code == 422

    async def test_offset_beyond_total_returns_empty_jobs(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """If offset is beyond the last result, jobs list is empty but total reflects all rows."""
        db_session.add(make_job())
        await db_session.flush()

        response = await client.get("/api/v1/jobs?offset=999")
        data = response.json()
        assert data["jobs"] == []
        assert data["total"] == 1  # Total still shows 1 existing job


# ══════════════════════════════════════════════════════════════════════════════
# TestUpdateJobStatusEndpoint — PATCH /api/v1/jobs/{id}
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestUpdateJobStatusEndpoint:
    """Tests for PATCH /api/v1/jobs/{id}."""

    async def test_mark_job_as_reviewed(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Happy path: marking a job as reviewed should return 200 with updated status."""
        job = make_job(status=JobStatus.SCORED, match_score=80.0)
        db_session.add(job)
        await db_session.flush()

        response = await client.patch(
            f"/api/v1/jobs/{job.id}",
            json={"status": "reviewed"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "reviewed"

    async def test_mark_job_as_ignored(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Marking a job as ignored should return 200 with status='ignored'."""
        job = make_job()
        db_session.add(job)
        await db_session.flush()

        response = await client.patch(
            f"/api/v1/jobs/{job.id}",
            json={"status": "ignored"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "ignored"

    async def test_returns_full_job_object(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """PATCH response should be a full JobResponse object, not just a status field."""
        job = make_job(title="SWE", company="Acme", match_score=75.0)
        db_session.add(job)
        await db_session.flush()

        response = await client.patch(
            f"/api/v1/jobs/{job.id}",
            json={"status": "reviewed"},
        )
        data = response.json()
        assert data["title"] == "SWE"
        assert data["company"] == "Acme"
        assert data["match_score"] == 75.0

    async def test_returns_404_for_nonexistent_job(self, client: AsyncClient) -> None:
        """Patching a job ID that doesn't exist should return 404 Not Found."""
        fake_id = uuid.uuid4()
        response = await client.patch(
            f"/api/v1/jobs/{fake_id}",
            json={"status": "reviewed"},
        )
        assert response.status_code == 404

    async def test_invalid_status_returns_422(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """
        Attempting to set status to 'applied' (agent-only transition) should return 422.
        The Literal["reviewed", "ignored"] type on PatchJobRequest enforces this.
        """
        job = make_job()
        db_session.add(job)
        await db_session.flush()

        response = await client.patch(
            f"/api/v1/jobs/{job.id}",
            json={"status": "applied"},  # Not allowed from dashboard
        )
        assert response.status_code == 422

    async def test_invalid_uuid_format_returns_422(self, client: AsyncClient) -> None:
        """Passing a non-UUID string as the job ID should return 422 Unprocessable Entity."""
        response = await client.patch(
            "/api/v1/jobs/not-a-valid-uuid",
            json={"status": "reviewed"},
        )
        assert response.status_code == 422

    async def test_missing_status_field_returns_422(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """PATCH body with no 'status' field should return 422."""
        job = make_job()
        db_session.add(job)
        await db_session.flush()

        response = await client.patch(
            f"/api/v1/jobs/{job.id}",
            json={},  # Empty body
        )
        assert response.status_code == 422

    async def test_soft_deleted_job_returns_404(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Patching a soft-deleted job should return 404 (as if it doesn't exist)."""
        from datetime import datetime, timezone

        job = make_job()
        job.deleted_at = datetime.now(timezone.utc)
        db_session.add(job)
        await db_session.flush()

        response = await client.patch(
            f"/api/v1/jobs/{job.id}",
            json={"status": "reviewed"},
        )
        assert response.status_code == 404

    async def test_empty_status_string_returns_422(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Empty string for status is invalid — should return 422."""
        job = make_job()
        db_session.add(job)
        await db_session.flush()

        response = await client.patch(
            f"/api/v1/jobs/{job.id}",
            json={"status": ""},
        )
        assert response.status_code == 422
