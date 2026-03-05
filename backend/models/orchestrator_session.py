"""
models/orchestrator_session.py — Stores state for each Orchestrator agent session.

WHY STORE SESSIONS IN THE DB (not just in-memory like pipeline.py)?
  The Orchestrator has an approval gate: it pauses after selecting jobs and waits
  for the user to approve before applying. That pause could last minutes or hours.
  In-memory state dies on server restart — we'd lose the pending job list.
  Storing sessions in PostgreSQL means the approval gate survives restarts and
  could survive multiple server processes (e.g. horizontal scaling).

FIELDS EXPLAINED:
  - status: the session's lifecycle state
      "running"              → agent loop is actively calling tools
      "waiting_for_approval" → paused at the apply gate; user must approve
      "complete"             → finished successfully
      "failed"               → hit max_turns or an unhandled error
  - goal: the natural-language goal the user submitted (e.g. "Find me 5 good jobs")
  - steps: JSON list of tool call + result entries — the agent's reasoning log
  - pending_job_ids: JSON list of job UUIDs waiting for apply approval
  - token_usage: cumulative tokens used across all Claude calls this session
  - result_summary: plain-English outcome written by Claude at the end
"""

import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from core.database import Base


class SessionStatus(str, PyEnum):
    """
    All possible lifecycle states for an orchestrator session.

    WHY str + PyEnum (instead of just PyEnum)?
      The `str` mixin lets these values be compared directly to strings and
      serialized to JSON without extra conversion. `session.status == "running"`
      works naturally alongside `session.status == SessionStatus.RUNNING`.
    """
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    COMPLETE = "complete"
    FAILED = "failed"


class OrchestratorSession(Base):
    """
    One record per orchestrator agent session.

    A session begins when the user submits a goal and ends when the agent
    reaches `end_turn`, hits the max_turns limit, or encounters a fatal error.
    Sessions that hit the approval gate stay alive (status=waiting_for_approval)
    until the user approves or abandons them.
    """

    __tablename__ = "orchestrator_sessions"

    # ── Primary Key ─────────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )

    # ── Goal ─────────────────────────────────────────────────────────────────
    # The user's natural-language goal for this session.
    goal: Mapped[str] = mapped_column(Text, nullable=False)

    # ── Status ───────────────────────────────────────────────────────────────
    status: Mapped[SessionStatus] = mapped_column(
        SAEnum(SessionStatus, name="session_status"),
        nullable=False,
        default=SessionStatus.RUNNING,
    )

    # ── Reasoning Log ────────────────────────────────────────────────────────
    # JSON array of step dicts. Each step looks like:
    #   {"tool": "check_db_state", "input": {...}, "result": {...}, "timestamp": "..."}
    # This is what the frontend shows as the "live reasoning log".
    # WHY JSON (not a separate table)?
    #   A separate StepLog table would be cleaner for querying, but adds complexity.
    #   For this use case — one session → one log to display → the JSON column is simpler.
    steps: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    # ── Approval Gate State ───────────────────────────────────────────────────
    # When the agent calls `request_apply_approval`, it saves the candidate job
    # UUIDs here. The API reads these to show the user the pending job cards.
    # Null when no approval is pending.
    pending_job_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # ── Token Usage ───────────────────────────────────────────────────────────
    # Running total of input + output tokens across all Claude calls in this session.
    # Tracked so the frontend can show cost transparency ("used 1,240 tokens").
    token_usage: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ── Outcome ───────────────────────────────────────────────────────────────
    # Plain-English summary written by the agent at the end of the session.
    # Null while the session is still running.
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

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

    # ── Indexes ───────────────────────────────────────────────────────────────
    __table_args__ = (
        # Index on status: the history endpoint filters by status,
        # and the approval endpoint checks status before processing.
        Index("ix_orchestrator_sessions_status", "status"),

        # Index on created_at: history endpoint orders by most recent first.
        Index("ix_orchestrator_sessions_created_at", "created_at"),
    )

    def __init__(self, **kwargs: object) -> None:
        """
        Apply Python-side defaults at instantiation time.
        See models/application.py __init__ for a full explanation of why this
        is necessary in SQLAlchemy 2.0 (mapped_column defaults are INSERT-time).
        """
        kwargs.setdefault("id", uuid.uuid4())
        kwargs.setdefault("status", SessionStatus.RUNNING)
        kwargs.setdefault("steps", [])
        kwargs.setdefault("token_usage", 0)
        super().__init__(**kwargs)

    def __repr__(self) -> str:
        status_val = self.status.value if self.status else "none"
        return f"<OrchestratorSession id={self.id!s:.8} status={status_val}>"
