"""
models/job.py — The Job model represents one scraped job listing.

LIFECYCLE OF A JOB RECORD:
  1. Scraper Agent creates it with status="new"
  2. Resume Match Agent updates match_score and status="scored"
  3. User reviews it in the dashboard (status="reviewed" or "ignored")
  4. Apply Agent sets status="applied" after submitting

DESIGN DECISIONS:
  - UUID primary key: generated in Python, no DB round-trip needed for inserts
  - source_url is UNIQUE: prevents the Scraper from inserting the same job twice
  - Index on (status, match_score): the dashboard's primary query is filtering
    by status and sorting by score — this index makes it instant even at 100k rows
  - `deleted_at` soft delete: never lose data; just hide it from queries
"""

import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import DateTime, Float, Index, String, Text, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from core.database import Base


class JobStatus(str, PyEnum):
    """
    All possible states a job can be in.
    Using `str` mixin means JobStatus.NEW == "new" (convenient for JSON serialization).
    """
    NEW = "new"           # Just scraped, not yet scored
    SCORED = "scored"     # Resume match score has been assigned
    REVIEWED = "reviewed" # User has looked at it in the dashboard
    IGNORED = "ignored"   # User dismissed it
    APPLIED = "applied"   # Apply Agent submitted an application
    FAILED = "failed"     # Apply Agent tried and failed


class JobSource(str, PyEnum):
    """Which job board this listing came from."""
    LINKEDIN = "linkedin"
    INDEED = "indeed"
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    OTHER = "other"


class Job(Base):
    """
    Represents one job listing scraped from a job board.
    One Job can have many Applications (if we want to re-apply or track multiple attempts).
    """

    __tablename__ = "jobs"

    # ── Primary Key ─────────────────────────────────────────────────────────
    # UUID: a 128-bit globally unique identifier.
    # WHY UUID over auto-increment int?
    #   With 10 Celery workers inserting simultaneously, UUIDs are generated in Python
    #   without a DB lock. Auto-increment requires a DB sequence lock — a bottleneck.
    # server_default=func.gen_random_uuid(): if somehow id isn't set in Python,
    #   the DB generates one itself as a safety net.
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )

    # ── Job Content ──────────────────────────────────────────────────────────
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    company: Mapped[str] = mapped_column(String(500), nullable=False)

    # The canonical job posting URL. UNIQUE prevents the same job being scraped twice.
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)

    location: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Full text of the job description. Text = unlimited length (vs String which caps at N).
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    salary_range: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # ── Source ───────────────────────────────────────────────────────────────
    source: Mapped[JobSource] = mapped_column(
        SAEnum(JobSource, name="job_source"),
        nullable=False,
        default=JobSource.OTHER,
    )

    # ── Status & Score ───────────────────────────────────────────────────────
    status: Mapped[JobStatus] = mapped_column(
        SAEnum(JobStatus, name="job_status"),
        nullable=False,
        default=JobStatus.NEW,
    )

    # Match score from Resume Match Agent: 0.0 to 100.0.
    # Null until the Resume Match Agent has processed this job.
    match_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Short explanation from Claude about why it scored this way.
    # Shown in the dashboard so you understand the reasoning.
    match_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Timestamps ───────────────────────────────────────────────────────────
    # server_default=func.now(): the DB sets these automatically if Python forgets.
    # onupdate=func.now(): updated_at is refreshed automatically on every UPDATE.
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
    # Soft delete: instead of DELETE FROM jobs, we set deleted_at.
    # Queries should always add WHERE deleted_at IS NULL.
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    # One Job can have many Application records (e.g., retries, multiple ATS systems).
    # lazy="selectin": when you load a Job, SQLAlchemy auto-fetches its applications
    # in a second efficient SELECT statement — avoids N+1 queries.
    applications: Mapped[list["Application"]] = relationship(
        "Application",
        back_populates="job",
        lazy="selectin",
    )

    # ── Table-Level Constraints & Indexes ────────────────────────────────────
    __table_args__ = (
        # Unique constraint on source_url prevents duplicate job listings.
        UniqueConstraint("source_url", name="uq_jobs_source_url"),

        # Composite index on (status, match_score DESC):
        # The dashboard's primary query is: "show me SCORED jobs, sorted by score."
        # This index makes that query instant even with millions of rows.
        Index("ix_jobs_status_match_score", "status", "match_score"),

        # Index on company: users often filter jobs by company name.
        Index("ix_jobs_company", "company"),

        # Index on source: filter by LinkedIn vs. Indeed.
        Index("ix_jobs_source", "source"),

        # Index on deleted_at: every query should filter WHERE deleted_at IS NULL.
        Index("ix_jobs_deleted_at", "deleted_at"),
    )

    def __init__(self, **kwargs: object) -> None:
        """
        Override __init__ to apply Python-side defaults at instantiation time.

        WHY WE NEED THIS:
          SQLAlchemy's `mapped_column(default=...)` applies defaults at INSERT time
          (when the ORM flushes to the DB), NOT when you call `Job(title="...")`.
          Without this override, a freshly constructed Job() has id=None, status=None.
          This override sets sensible defaults so objects are immediately usable in Python.

        NOTE: SQLAlchemy bypasses __init__ when loading objects FROM the database
          (it uses __new__ instead). So this only runs for Python-created objects,
          not for DB-reconstructed ones. No conflict with DB values.
        """
        kwargs.setdefault("id", uuid.uuid4())
        kwargs.setdefault("status", JobStatus.NEW)
        super().__init__(**kwargs)

    def __repr__(self) -> str:
        status_val = self.status.value if self.status else "none"
        return f"<Job id={self.id!s:.8} title={self.title!r} company={self.company!r} status={status_val}>"
