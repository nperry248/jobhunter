"""
models/application.py — Tracks each attempt to submit a job application.

WHY THIS IS SEPARATE FROM JOB:
  A Job is "this listing exists at Acme Corp".
  An Application is "we tried to apply to Acme Corp on Tuesday at 2pm and it succeeded."
  These are different things: you might apply multiple times (retry after failure),
  or the same job might appear on two boards and you apply via one.

FIELDS EXPLAINED:
  - status: tracks apply attempt lifecycle (pending → submitted or failed)
  - applied_at: exact timestamp of successful submission
  - screenshot_path: Playwright takes a screenshot on apply — stored for proof/debugging
  - error_message: if the attempt failed, why
"""

import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from core.database import Base


class ApplicationStatus(str, PyEnum):
    """All possible states for a job application attempt."""
    PENDING = "pending"       # Queued for apply agent, not started yet
    IN_PROGRESS = "in_progress"  # Apply agent is currently working on it
    SUBMITTED = "submitted"   # Successfully submitted
    FAILED = "failed"         # Apply agent encountered an error
    MANUAL_REQUIRED = "manual_required"  # ATS couldn't be automated, needs human


class TrackingStatus(str, PyEnum):
    """
    Where the user is in the hiring process AFTER applying.

    This is separate from ApplicationStatus, which tracks the apply agent's work.
    TrackingStatus tracks the human's job-search journey — updated manually via
    the dropdown on the Applications page.

    Kept intentionally simple: four stages cover the full arc without over-granularity.
    "Interview" covers any stage (phone screen, technical, final round) — the user
    doesn't need to distinguish between them for tracking purposes.
    """
    APPLIED = "applied"       # Submitted, waiting to hear back
    INTERVIEW = "interview"   # Any interview stage (phone screen, technical, final)
    OFFER = "offer"           # Received an offer
    REJECTED = "rejected"     # Rejected at any stage


class Application(Base):
    """
    One record per application attempt. Created by the Apply Agent.
    Foreign key to Job links it back to the listing being applied to.
    """

    __tablename__ = "applications"

    # ── Primary Key ─────────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )

    # ── Foreign Key to Job ───────────────────────────────────────────────────
    # NOTE: ForeignKey creates a referential integrity constraint in PostgreSQL.
    # If you try to delete a Job that has Applications, PostgreSQL will refuse
    # (or cascade, depending on config). This prevents orphaned application records.
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )

    # ── Status ───────────────────────────────────────────────────────────────
    status: Mapped[ApplicationStatus] = mapped_column(
        SAEnum(ApplicationStatus, name="application_status"),
        nullable=False,
        default=ApplicationStatus.PENDING,
    )

    # ── Tracking Status (user-managed) ───────────────────────────────────────
    # Tracks where the user is in the hiring process AFTER submitting.
    # The user updates this manually via the Applications page dropdown.
    # Starts as APPLIED (the default after any successful submission).
    tracking_status: Mapped[TrackingStatus] = mapped_column(
        SAEnum(TrackingStatus, name="tracking_status"),
        nullable=False,
        default=TrackingStatus.APPLIED,
    )

    # ── Apply Attempt Details ────────────────────────────────────────────────
    # Null until successfully submitted
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Path to the Playwright screenshot taken after submission
    # (stored on local disk in Phase 1, could move to S3 later)
    screenshot_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # If the apply attempt failed, store the error for debugging
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Which ATS system was used (greenhouse, lever, workday, etc.)
    ats_system: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # ── Timestamps ───────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    # Many Applications belong to one Job.
    job: Mapped["Job"] = relationship("Job", back_populates="applications")

    # ── Indexes ───────────────────────────────────────────────────────────────
    __table_args__ = (
        # Index on job_id: the FK column always needs an index.
        # Without it, queries like "give me all applications for job X" do a full table scan.
        Index("ix_applications_job_id", "job_id"),

        # Index on status: dashboard queries frequently filter by status.
        Index("ix_applications_status", "status"),
    )

    def __init__(self, **kwargs: object) -> None:
        """
        Apply Python-side defaults at instantiation time.
        See Job.__init__ for full explanation of why this is necessary in SQLAlchemy 2.0.
        """
        kwargs.setdefault("id", uuid.uuid4())
        kwargs.setdefault("status", ApplicationStatus.PENDING)
        kwargs.setdefault("tracking_status", TrackingStatus.APPLIED)
        super().__init__(**kwargs)

    def __repr__(self) -> str:
        status_val = self.status.value if self.status else "none"
        return f"<Application id={self.id!s:.8} job_id={self.job_id!s:.8} status={status_val}>"
