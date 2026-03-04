"""
api/routes/jobs.py — HTTP endpoints for the Jobs resource.

ENDPOINTS:
  GET  /api/v1/jobs         — Paginated, filterable list of jobs
  PATCH /api/v1/jobs/{id}   — Update a job's status (reviewed or ignored only)

WHY THESE TWO ENDPOINTS:
  The React dashboard needs to:
  1. Show a sorted, filterable list of jobs with match scores (GET)
  2. Let the user mark jobs as reviewed or ignored (PATCH)

  That's it. The other status transitions (new→scored, scored→applied, etc.)
  happen through agents — they're not user-triggered.

CONCEPT — Pydantic schemas for API I/O:
  FastAPI uses Pydantic models (defined below) to:
    - VALIDATE incoming request data (PATCH body must have a valid status)
    - SERIALIZE outgoing response data (Job ORM → JSON)
  This is different from SQLAlchemy models (which map to DB rows).
  A common pattern: SQLAlchemy model for DB, Pydantic schema for API.

CONCEPT — APIRouter:
  We define routes in a separate file and use APIRouter.
  The main app (api/main.py) includes this router with a prefix (/api/v1/jobs).
  This keeps route files focused on one resource and prevents api/main.py from
  becoming unmanageably large.
"""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from models.job import Job, JobStatus

router = APIRouter()


# ── Response Schemas ──────────────────────────────────────────────────────────
# CONCEPT — Response schemas vs DB models:
#   We could return SQLAlchemy model objects directly, but that has problems:
#   1. SQLAlchemy models have lazy-loaded relationships that can fire unexpected queries
#   2. They expose internal fields we don't want in the API (deleted_at, etc.)
#   3. Pydantic schemas give us precise control over the JSON shape
#
# `model_config = ConfigDict(from_attributes=True)` tells Pydantic to read
# attribute values from SQLAlchemy objects (ORM mode), not just dicts.

class JobResponse(BaseModel):
    """
    Shape of a single job object in API responses.
    Only exposes fields the frontend needs — not internal DB fields.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    company: str
    source_url: str
    location: str | None
    description: str | None
    salary_range: str | None
    source: str        # "greenhouse" or "lever" — serialized as string for JSON
    status: str        # "new", "scored", "reviewed", etc.
    match_score: float | None
    match_reasoning: str | None


class JobListResponse(BaseModel):
    """
    Envelope for paginated job list responses.

    WHY AN ENVELOPE:
      If we returned just a list, the frontend would have no way to know:
        - How many total jobs exist (to render pagination)
        - What offset we're at
      The envelope carries that metadata alongside the data.

    Format:
      { "jobs": [...], "total": 245, "limit": 20, "offset": 0 }
    """
    jobs: list[JobResponse]
    total: int    # total count matching the filter (not just this page)
    limit: int    # how many results were requested
    offset: int   # how far into the result set we are


class PatchJobRequest(BaseModel):
    """
    Request body for PATCH /jobs/{id}.

    Allowed transitions from the dashboard:
      scored   → reviewed  (user marks a job as reviewed)
      scored   → ignored   (user dismisses a job)
      reviewed → scored    (user undoes a review — goes back to scored)
    """
    status: Literal["reviewed", "ignored", "scored"]


# ── GET /api/v1/jobs ──────────────────────────────────────────────────────────

@router.get("", response_model=JobListResponse)
async def list_jobs(
    # ── Pagination query params ────────────────────────────────────────────
    # `Query(...)` declares a query parameter with validation + docs.
    # ge=1 means "greater than or equal to 1" — prevents limit=0 or negative.
    # le=200 caps the page size so nobody can fetch 10,000 rows at once.
    limit: int = Query(default=20, ge=1, le=200, description="Number of results per page"),
    offset: int = Query(default=0, ge=0, description="Number of results to skip"),

    # ── Filter query params ───────────────────────────────────────────────
    # Optional: filter by status. None = return all statuses.
    status: str | None = Query(default=None, description="Filter by job status"),

    # Optional: filter by company (case-insensitive substring match)
    company: str | None = Query(default=None, description="Filter by company name (partial match)"),

    # Optional: only return jobs with match_score >= min_score
    min_score: float | None = Query(default=None, ge=0, le=100, description="Minimum match score"),

    # ── Dependency injection ──────────────────────────────────────────────
    # FastAPI calls get_db(), gets a session, and passes it in automatically.
    # In tests, this is overridden to use the test DB session.
    db: AsyncSession = Depends(get_db),
) -> JobListResponse:
    """
    Return a paginated, filterable list of jobs ordered by match score.

    Results are ordered by match_score DESC NULLS LAST, then created_at DESC.
    This means high-scoring jobs appear first; unscored jobs (null score) appear last.
    """
    # ── Build the base query ───────────────────────────────────────────────────
    # We always exclude soft-deleted rows.
    base_query = select(Job).where(Job.deleted_at.is_(None))

    # ── Apply optional filters ─────────────────────────────────────────────────
    if status is not None:
        # Validate that the provided status string is a known JobStatus value.
        # If not, return 400 Bad Request with a clear error message.
        try:
            status_enum = JobStatus(status)
        except ValueError:
            valid = [s.value for s in JobStatus]
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status '{status}'. Must be one of: {valid}",
            )
        base_query = base_query.where(Job.status == status_enum)

    if company is not None:
        # ilike = case-insensitive LIKE. The % wildcards allow substring matching.
        # e.g. company="air" matches "Airbnb", "Repair.com", "DayAir" etc.
        base_query = base_query.where(Job.company.ilike(f"%{company}%"))

    if min_score is not None:
        # NOTE: Jobs with match_score=NULL are excluded when min_score is set.
        # This is intentional — unscored jobs don't have a score to compare.
        base_query = base_query.where(Job.match_score >= min_score)

    # ── Count total matching rows (for pagination metadata) ───────────────────
    # We run a COUNT query BEFORE applying LIMIT/OFFSET so we get the full total.
    # CONCEPT — Subquery for count:
    #   We wrap the base_query as a subquery and count its rows.
    #   This is more reliable than counting with GROUP BY when there are joins.
    count_query = select(func.count()).select_from(base_query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    # ── Apply ordering + pagination ────────────────────────────────────────────
    # ORDER BY match_score DESC NULLS LAST: jobs with high scores first,
    # unscored jobs (null) pushed to the end.
    # Then by created_at DESC: among same-scored jobs, newest first.
    jobs_query = (
        base_query
        .order_by(
            Job.match_score.desc().nulls_last(),
            Job.created_at.desc(),
        )
        .limit(limit)
        .offset(offset)
    )

    jobs_result = await db.execute(jobs_query)
    jobs = list(jobs_result.scalars().all())

    return JobListResponse(
        jobs=[JobResponse.model_validate(job) for job in jobs],
        total=total,
        limit=limit,
        offset=offset,
    )


# ── DELETE /api/v1/jobs ───────────────────────────────────────────────────────

@router.delete("", status_code=204)
async def clear_all_jobs(db: AsyncSession = Depends(get_db)) -> Response:
    """
    Hard-delete all jobs from the database.

    Used by the dashboard "Clear All Jobs" button to wipe the slate before a
    fresh scrape run. Returns 204 No Content on success.

    WHY HARD DELETE (not soft delete):
      This is a deliberate user action to reset the job list. Soft deletes
      would just hide the rows but keep them consuming space. A hard DELETE
      is cleaner and the data can always be re-scraped.
    """
    await db.execute(delete(Job))
    await db.flush()
    return Response(status_code=204)


# ── PATCH /api/v1/jobs/{id} ───────────────────────────────────────────────────

@router.patch("/{job_id}", response_model=JobResponse)
async def update_job_status(
    job_id: uuid.UUID,
    body: PatchJobRequest,
    db: AsyncSession = Depends(get_db),
) -> JobResponse:
    """
    Update a job's status to 'reviewed' or 'ignored'.

    Called when the user clicks "Mark Reviewed" or "Dismiss" in the dashboard.
    Returns the updated job object so the frontend can update its local state
    without needing to refetch the list.

    Raises:
        404: If no job with this ID exists (or it's soft-deleted).
        400: If the requested status transition is not allowed (handled by Pydantic).
    """
    # ── Fetch the job ──────────────────────────────────────────────────────────
    result = await db.execute(
        select(Job)
        .where(Job.id == job_id)
        .where(Job.deleted_at.is_(None))
    )
    job = result.scalar_one_or_none()

    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    # ── Apply the status update ────────────────────────────────────────────────
    # Convert the string from the request body ("reviewed") to the enum (JobStatus.REVIEWED).
    job.status = JobStatus(body.status)

    # `db.flush()` sends the UPDATE to PostgreSQL but does NOT commit yet.
    # The actual commit happens in `get_db()` after this route handler returns.
    # WHY flush here? So we can read back the updated values in the response.
    await db.flush()

    # Return the updated job — the frontend uses this to update the UI immediately
    # without waiting for a full list refetch.
    return JobResponse.model_validate(job)
