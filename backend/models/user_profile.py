"""
models/user_profile.py — Stores the user's personal info for auto-applying.

WHY IN THE DATABASE (not a JSON file):
  Option 1 — JSON file: Simple, no DB. But breaks if two workers try to read/write
                         simultaneously, hard to version, no type validation.
  Option 2 — Database (chosen): Single source of truth accessible to all Celery workers,
                                  versioned, queryable. Slightly more complex but scales.

SECURITY NOTE:
  This table contains personal information (name, email, phone).
  In a production multi-user system, this would need encryption at rest.
  For v1 (local single-user), the DB is on localhost — acceptable risk.
  Never store passwords here — use a secrets manager for anything credential-like.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from core.database import Base


class UserProfile(Base):
    """
    The user's profile: personal info, resume location, and job preferences.
    There is only ever ONE row in this table (single-user system).
    """

    __tablename__ = "user_profiles"

    # ── Primary Key ─────────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )

    # ── Identity ──────────────────────────────────────────────────────────────
    full_name: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    email: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    github_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    portfolio_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    location: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # ── Resume ────────────────────────────────────────────────────────────────
    # Path to the resume PDF on the local filesystem.
    # The Resume Match Agent reads this file when scoring jobs.
    resume_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # Cached extracted text from the resume PDF.
    # Storing it here avoids re-parsing the PDF on every job scoring run.
    resume_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Job Preferences ───────────────────────────────────────────────────────
    # These are used by the Scraper Agent to filter listings before they hit the DB.

    # JSON-encoded list of target job titles, e.g. '["Software Engineer", "SWE Intern"]'
    # WHY JSON string instead of a separate table?
    #   A separate `job_titles` table would require a JOIN every time.
    #   For a small list of strings, storing as JSON is simpler and fast enough.
    target_titles: Mapped[str | None] = mapped_column(Text, nullable=True, default="[]")

    # JSON-encoded list of target locations, e.g. '["San Francisco", "Remote"]'
    target_locations: Mapped[str | None] = mapped_column(Text, nullable=True, default="[]")

    # JSON-encoded list of companies to skip, e.g. '["Company I Hate Inc"]'
    company_blocklist: Mapped[str | None] = mapped_column(Text, nullable=True, default="[]")

    # Whether to target internship positions
    target_internships: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Whether to target new-grad (0–2 years exp) positions
    target_new_grad: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Auto-apply to jobs above this score (0–100). Overrides the global setting.
    # Null means "use the global MATCH_SCORE_THRESHOLD from settings"
    auto_apply_threshold: Mapped[int | None] = mapped_column(nullable=True)

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

    def __init__(self, **kwargs: object) -> None:
        """
        Apply Python-side defaults at instantiation time.
        See Job.__init__ for full explanation of why this is necessary in SQLAlchemy 2.0.
        """
        kwargs.setdefault("id", uuid.uuid4())
        kwargs.setdefault("target_internships", True)
        kwargs.setdefault("target_new_grad", True)
        kwargs.setdefault("target_titles", "[]")
        kwargs.setdefault("target_locations", "[]")
        kwargs.setdefault("company_blocklist", "[]")
        super().__init__(**kwargs)

    def __repr__(self) -> str:
        return f"<UserProfile id={self.id!s:.8} name={self.full_name!r} email={self.email!r}>"
