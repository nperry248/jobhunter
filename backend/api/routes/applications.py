"""
api/routes/applications.py — HTTP endpoints for the Applications resource.

ENDPOINTS:
  GET   /api/v1/applications        — Paginated list of applications with job details
  PATCH /api/v1/applications/{id}   — Update an application's tracking_status

DESIGN:
  Applications are created automatically by the Apply Agent — users never create
  them manually. What users DO control is the `tracking_status` field: as they
  hear back from companies (phone screen, rejection, offer), they update it here.

  Each application response includes the full job details (title, company, score,
  url) pulled from the Application → Job relationship, so the frontend doesn't
  need to make a second request to /jobs for display info.
"""

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.database import get_db
from models.application import Application, TrackingStatus

router = APIRouter()


# ── Response Schemas ──────────────────────────────────────────────────────────

class JobSummary(BaseModel):
    """
    Minimal job fields embedded in each application response.
    We include only what the Applications page needs — not the full description.
    """
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    company: str
    source_url: str
    location: str | None
    match_score: float | None
    match_reasoning: str | None
    source: str


class ApplicationResponse(BaseModel):
    """
    Shape of a single application object in API responses.

    Includes the parent job's details (via the relationship) so the frontend
    can render the full card without a second API call.
    """
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_id: uuid.UUID
    status: str           # apply agent outcome: submitted | failed | pending
    tracking_status: str  # user-managed interview stage
    applied_at: datetime | None
    screenshot_path: str | None
    error_message: str | None
    ats_system: str | None
    created_at: datetime
    updated_at: datetime

    # Nested job details — populated from the SQLAlchemy relationship
    job: JobSummary


class ApplicationListResponse(BaseModel):
    """Paginated envelope for the applications list."""
    applications: list[ApplicationResponse]
    total: int
    limit: int
    offset: int


class PatchApplicationRequest(BaseModel):
    """
    Request body for PATCH /applications/{id}.

    Only tracking_status is user-editable — the apply agent outcome (status)
    is set by automation and should not be overridable from the UI.
    """
    tracking_status: Literal["applied", "interview", "offer", "rejected"]


# ── GET /api/v1/applications ──────────────────────────────────────────────────

@router.get("", response_model=ApplicationListResponse)
async def list_applications(
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),

    # Filter by tracking_status — lets the frontend show e.g. only "offer" rows
    tracking_status: str | None = Query(default=None, description="Filter by tracking status"),

    db: AsyncSession = Depends(get_db),
) -> ApplicationListResponse:
    """
    Return a paginated list of applications, newest first, with job details embedded.

    QUERY STRATEGY — selectinload:
      We use selectinload(Application.job) so SQLAlchemy fetches all the related
      Job rows in a single second query (SELECT * FROM jobs WHERE id IN (...))
      rather than one query per application (the N+1 problem).
    """
    base_query = (
        select(Application)
        .options(selectinload(Application.job))
    )

    if tracking_status is not None:
        try:
            status_enum = TrackingStatus(tracking_status)
        except ValueError:
            valid = [s.value for s in TrackingStatus]
            raise HTTPException(
                status_code=400,
                detail=f"Invalid tracking_status '{tracking_status}'. Must be one of: {valid}",
            )
        base_query = base_query.where(Application.tracking_status == status_enum)

    # Total count (before pagination) for the envelope
    count_query = select(func.count()).select_from(base_query.subquery())
    total = (await db.execute(count_query)).scalar_one()

    result = await db.execute(
        base_query
        .order_by(Application.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    applications = list(result.scalars().all())

    return ApplicationListResponse(
        applications=[ApplicationResponse.model_validate(app) for app in applications],
        total=total,
        limit=limit,
        offset=offset,
    )


# ── PATCH /api/v1/applications/{id} ──────────────────────────────────────────

@router.patch("/{application_id}", response_model=ApplicationResponse)
async def update_application_tracking(
    application_id: uuid.UUID,
    body: PatchApplicationRequest,
    db: AsyncSession = Depends(get_db),
) -> ApplicationResponse:
    """
    Update an application's tracking_status.

    Called when the user changes the status dropdown on the Applications page.
    Returns the updated application (with job details) so the frontend can
    update its local state without a refetch.
    """
    result = await db.execute(
        select(Application)
        .options(selectinload(Application.job))
        .where(Application.id == application_id)
    )
    application = result.scalar_one_or_none()

    if application is None:
        raise HTTPException(status_code=404, detail=f"Application {application_id} not found")

    application.tracking_status = TrackingStatus(body.tracking_status)
    await db.flush()

    # Re-fetch after flush: `onupdate=func.now()` on updated_at causes SQLAlchemy
    # to expire the column after any UPDATE. Accessing it via Pydantic's model_validate
    # would trigger a lazy async load in a sync context → MissingGreenlet error.
    # Re-querying returns a fresh object with all columns loaded and job relationship eager-loaded.
    refreshed = await db.execute(
        select(Application)
        .options(selectinload(Application.job))
        .where(Application.id == application_id)
    )
    return ApplicationResponse.model_validate(refreshed.scalar_one())


# ── DELETE /api/v1/applications/{id} ─────────────────────────────────────────

@router.delete("/{application_id}", status_code=204)
async def delete_application(
    application_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """
    Hard-delete an application record.

    Called when the user removes an entry from the Applications page.
    Returns 204 No Content on success.

    WHY HARD DELETE (not soft delete):
      Applications are audit records of apply attempts. Removing one is a deliberate
      user action — they want it gone from the list. Soft-deleting adds complexity
      (every query needs a filter) for no benefit in a single-user system.
      The underlying Job record is untouched — only the Application row is removed.
    """
    result = await db.execute(
        delete(Application).where(Application.id == application_id).returning(Application.id)
    )
    deleted_id = result.scalar_one_or_none()

    if deleted_id is None:
        raise HTTPException(status_code=404, detail=f"Application {application_id} not found")

    await db.flush()
    return Response(status_code=204)
