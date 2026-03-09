"""
tests/integration/test_api_applications.py — API integration tests for /api/v1/applications.

WHAT WE'RE TESTING:
  - GET /api/v1/applications: returns applications with embedded job details,
    pagination, tracking_status filter, empty state
  - PATCH /api/v1/applications/{id}: happy path, 404, invalid tracking_status
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from models.application import Application, ApplicationStatus, TrackingStatus

# TrackingStatus now has 4 values: APPLIED, INTERVIEW, OFFER, REJECTED
from models.job import Job, JobSource, JobStatus


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_job(title: str = "SWE", company: str = "Acme") -> Job:
    return Job(
        title=title,
        company=company,
        source_url=f"https://boards.greenhouse.io/acme/jobs/{uuid.uuid4()}",
        source=JobSource.GREENHOUSE,
        status=JobStatus.APPLIED,
        match_score=82.0,
        match_reasoning="Strong Python match.",
    )


def make_application(
    job: Job,
    status: ApplicationStatus = ApplicationStatus.SUBMITTED,
    tracking_status: TrackingStatus = TrackingStatus.APPLIED,
) -> Application:
    return Application(
        job_id=job.id,
        status=status,
        tracking_status=tracking_status,
        ats_system="greenhouse",
    )


# ── GET /api/v1/applications ──────────────────────────────────────────────────

class TestListApplications:
    async def test_empty_list(self, client: AsyncClient):
        response = await client.get("/api/v1/applications")
        assert response.status_code == 200
        data = response.json()
        assert data["applications"] == []
        assert data["total"] == 0

    async def test_returns_application_with_job_details(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        job = make_job(title="Backend Engineer", company="Stripe")
        db_session.add(job)
        await db_session.flush()

        app = make_application(job)
        db_session.add(app)
        await db_session.flush()

        response = await client.get("/api/v1/applications")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1

        item = data["applications"][0]
        assert item["id"] == str(app.id)
        assert item["status"] == "submitted"
        assert item["tracking_status"] == "applied"
        # Job details embedded
        assert item["job"]["title"] == "Backend Engineer"
        assert item["job"]["company"] == "Stripe"
        assert item["job"]["match_score"] == 82.0

    async def test_returns_both_applications(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        job1 = make_job(company="Alpha")
        job2 = make_job(company="Beta")
        db_session.add_all([job1, job2])
        await db_session.flush()

        app1 = make_application(job1)
        app2 = make_application(job2)
        db_session.add_all([app1, app2])
        await db_session.flush()

        response = await client.get("/api/v1/applications")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        companies = {a["job"]["company"] for a in data["applications"]}
        assert companies == {"Alpha", "Beta"}

    async def test_filter_by_tracking_status(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        job1 = make_job(company="A")
        job2 = make_job(company="B")
        db_session.add_all([job1, job2])
        await db_session.flush()

        app1 = make_application(job1, tracking_status=TrackingStatus.INTERVIEW)
        app2 = make_application(job2, tracking_status=TrackingStatus.REJECTED)
        db_session.add_all([app1, app2])
        await db_session.flush()

        response = await client.get("/api/v1/applications?tracking_status=interview")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["applications"][0]["tracking_status"] == "interview"

    async def test_invalid_tracking_status_returns_400(self, client: AsyncClient):
        response = await client.get("/api/v1/applications?tracking_status=promoted")
        assert response.status_code == 400
        assert "promoted" in response.json()["detail"]

    async def test_pagination(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        jobs = [make_job(company=f"Co{i}") for i in range(5)]
        db_session.add_all(jobs)
        await db_session.flush()
        apps = [make_application(j) for j in jobs]
        db_session.add_all(apps)
        await db_session.flush()

        response = await client.get("/api/v1/applications?limit=2&offset=0")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 5
        assert len(data["applications"]) == 2
        assert data["limit"] == 2
        assert data["offset"] == 0

        response2 = await client.get("/api/v1/applications?limit=2&offset=2")
        assert len(response2.json()["applications"]) == 2

    async def test_failed_application_included(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        job = make_job()
        db_session.add(job)
        await db_session.flush()
        app = make_application(job, status=ApplicationStatus.FAILED)
        app.error_message = "Could not find submit button"
        db_session.add(app)
        await db_session.flush()

        response = await client.get("/api/v1/applications")
        assert response.status_code == 200
        item = response.json()["applications"][0]
        assert item["status"] == "failed"
        assert item["error_message"] == "Could not find submit button"


# ── PATCH /api/v1/applications/{id} ──────────────────────────────────────────

class TestPatchApplication:
    async def test_update_tracking_status(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        job = make_job()
        db_session.add(job)
        await db_session.flush()
        app = make_application(job)
        db_session.add(app)
        await db_session.flush()

        response = await client.patch(
            f"/api/v1/applications/{app.id}",
            json={"tracking_status": "interview"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["tracking_status"] == "interview"
        # Job details still embedded in response
        assert data["job"]["title"] == job.title

    async def test_all_tracking_statuses_accepted(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        statuses = ["applied", "interview", "offer", "rejected"]
        for status in statuses:
            job = make_job()
            db_session.add(job)
            await db_session.flush()
            app = make_application(job)
            db_session.add(app)
            await db_session.flush()

            response = await client.patch(
                f"/api/v1/applications/{app.id}",
                json={"tracking_status": status},
            )
            assert response.status_code == 200, f"Failed for status: {status}"
            assert response.json()["tracking_status"] == status

    async def test_404_for_unknown_id(self, client: AsyncClient):
        response = await client.patch(
            f"/api/v1/applications/{uuid.uuid4()}",
            json={"tracking_status": "offer"},
        )
        assert response.status_code == 404

    async def test_invalid_tracking_status_returns_422(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        job = make_job()
        db_session.add(job)
        await db_session.flush()
        app = make_application(job)
        db_session.add(app)
        await db_session.flush()

        response = await client.patch(
            f"/api/v1/applications/{app.id}",
            json={"tracking_status": "promoted"},
        )
        # Pydantic's Literal validator rejects unknown values with 422 Unprocessable Entity
        assert response.status_code == 422


# ── DELETE /api/v1/applications/{id} ─────────────────────────────────────────

class TestDeleteApplication:
    async def test_delete_removes_application(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        job = make_job()
        db_session.add(job)
        await db_session.flush()
        app = make_application(job)
        db_session.add(app)
        await db_session.flush()

        response = await client.delete(f"/api/v1/applications/{app.id}")
        assert response.status_code == 204

        # Verify it's gone
        list_response = await client.get("/api/v1/applications")
        assert list_response.json()["total"] == 0

    async def test_delete_404_for_unknown_id(self, client: AsyncClient):
        response = await client.delete(f"/api/v1/applications/{uuid.uuid4()}")
        assert response.status_code == 404
