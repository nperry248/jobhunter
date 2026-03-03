"""
tests/integration/test_resume_match_pipeline.py — Integration tests for the Resume Match Agent.

WHAT WE'RE TESTING:
  - The full agent pipeline: load_resume_text → fetch_new_jobs → score_job → update_job_score
  - That agent functions interact correctly with the test database
  - That Claude API calls are properly mocked (never hit real API in tests)

WHAT WE'RE NOT TESTING HERE:
  - The scoring logic itself (tested in test_resume_match.py)
  - The PDF parser (tested in test_resume_parser.py)

MOCKING STRATEGY (per CLAUDE.md):
  mocker.patch("anthropic.Anthropic", return_value=mock_client)
  This replaces the Anthropic class in the agents.resume_match module so that
  score_job() returns canned responses without making real HTTP calls.

CONCEPT — Integration tests vs unit tests:
  Unit tests test pure functions in isolation.
  Integration tests test how multiple components work together:
    - Does the agent correctly fetch jobs from the DB?
    - Does it write scores back to the correct rows?
    - Does it handle DB errors gracefully?
  They require a real (test) database but still mock external APIs.
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from agents.resume_match import (
    MatchResult,
    fetch_new_jobs,
    load_resume_text,
    run,
    update_job_score,
)
from models.job import Job, JobStatus
from models.user_profile import UserProfile


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_job(
    title: str = "SWE Intern",
    company: str = "Acme",
    description: str = "Python, SQL, REST APIs, cloud experience",
    status: JobStatus = JobStatus.NEW,
) -> Job:
    """Create a Job ORM object with sensible defaults for testing."""
    return Job(
        title=title,
        company=company,
        source_url=f"https://example.com/jobs/{uuid.uuid4()}",
        description=description,
        status=status,
    )


def make_mock_claude_client(score: int = 80, reasoning: str = "Strong match.") -> MagicMock:
    """
    Build a mock Anthropic client that returns a canned Claude response.

    The mock mirrors the real Anthropic SDK's response structure:
      client.messages.create() → message object
      message.content → list of content blocks
      message.content[0].text → the string response
    """
    mock_content_block = MagicMock()
    mock_content_block.text = f'{{"score": {score}, "reasoning": "{reasoning}"}}'

    mock_message = MagicMock()
    mock_message.content = [mock_content_block]

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_message

    return mock_client


# ══════════════════════════════════════════════════════════════════════════════
# TestFetchNewJobs
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestFetchNewJobs:
    """Tests for fetch_new_jobs() — the DB query function."""

    async def test_returns_only_new_jobs(self, db_session: AsyncSession) -> None:
        """fetch_new_jobs() should only return jobs with status=NEW."""
        new_job = make_job(status=JobStatus.NEW)
        scored_job = make_job(status=JobStatus.SCORED)
        reviewed_job = make_job(status=JobStatus.REVIEWED)
        db_session.add_all([new_job, scored_job, reviewed_job])
        await db_session.flush()

        jobs = await fetch_new_jobs(db_session)
        returned_ids = {j.id for j in jobs}

        assert new_job.id in returned_ids
        assert scored_job.id not in returned_ids
        assert reviewed_job.id not in returned_ids

    async def test_excludes_soft_deleted_jobs(self, db_session: AsyncSession) -> None:
        """Soft-deleted jobs (deleted_at is set) must not appear in results."""
        from datetime import datetime, timezone

        active_job = make_job()
        deleted_job = make_job()
        deleted_job.deleted_at = datetime.now(timezone.utc)

        db_session.add_all([active_job, deleted_job])
        await db_session.flush()

        jobs = await fetch_new_jobs(db_session)
        returned_ids = {j.id for j in jobs}

        assert active_job.id in returned_ids
        assert deleted_job.id not in returned_ids

    async def test_returns_empty_list_when_no_new_jobs(self, db_session: AsyncSession) -> None:
        """If no NEW jobs exist, fetch_new_jobs() returns an empty list without error."""
        # Add only a scored job
        scored_job = make_job(status=JobStatus.SCORED)
        db_session.add(scored_job)
        await db_session.flush()

        jobs = await fetch_new_jobs(db_session)
        assert jobs == []


# ══════════════════════════════════════════════════════════════════════════════
# TestUpdateJobScore
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestUpdateJobScore:
    """Tests for update_job_score() — the DB write function."""

    async def test_updates_match_score_and_reasoning(self, db_session: AsyncSession) -> None:
        """update_job_score() should write the score and reasoning to the DB row."""
        job = make_job()
        db_session.add(job)
        await db_session.flush()

        await update_job_score(job.id, 85.0, "Strong Python match.", db_session)
        await db_session.flush()

        # Re-fetch the job to confirm DB was updated
        from sqlalchemy import select
        result = await db_session.execute(select(Job).where(Job.id == job.id))
        updated_job = result.scalar_one()

        assert updated_job.match_score == 85.0
        assert updated_job.match_reasoning == "Strong Python match."

    async def test_sets_status_to_scored(self, db_session: AsyncSession) -> None:
        """After scoring, job status must change from NEW to SCORED."""
        job = make_job(status=JobStatus.NEW)
        db_session.add(job)
        await db_session.flush()

        await update_job_score(job.id, 72.0, "Decent match.", db_session)
        await db_session.flush()

        from sqlalchemy import select
        result = await db_session.execute(select(Job).where(Job.id == job.id))
        updated_job = result.scalar_one()
        assert updated_job.status == JobStatus.SCORED

    async def test_clamps_score_above_100(self, db_session: AsyncSession) -> None:
        """update_job_score() should clamp scores above 100 before writing."""
        job = make_job()
        db_session.add(job)
        await db_session.flush()

        await update_job_score(job.id, 150.0, "Off the charts!", db_session)
        await db_session.flush()

        from sqlalchemy import select
        result = await db_session.execute(select(Job).where(Job.id == job.id))
        updated_job = result.scalar_one()
        assert updated_job.match_score == 100.0


# ══════════════════════════════════════════════════════════════════════════════
# TestLoadResumeText
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestLoadResumeText:
    """Tests for load_resume_text() — the resume loading function."""

    async def test_loads_cached_text_from_profile(self, db_session: AsyncSession) -> None:
        """
        If UserProfile already has resume_text, load_resume_text() should return it
        directly without calling parse_pdf() (which would require a real file).
        """
        profile = UserProfile(
            full_name="Test User",
            email="test@example.com",
            resume_text="My cached resume text content.",
        )
        db_session.add(profile)
        await db_session.flush()

        text = await load_resume_text(resume_path=None, session=db_session)
        assert text == "My cached resume text content."

    async def test_raises_if_no_profile_and_no_path(self, db_session: AsyncSession) -> None:
        """
        If there's no UserProfile and no explicit path, load_resume_text()
        should raise ValueError with a helpful message.
        """
        with pytest.raises(ValueError, match="No UserProfile found"):
            await load_resume_text(resume_path=None, session=db_session)

    async def test_raises_if_profile_has_no_resume(self, db_session: AsyncSession) -> None:
        """
        If UserProfile exists but has neither resume_path nor resume_text,
        load_resume_text() should raise ValueError.
        """
        profile = UserProfile(
            full_name="Test User",
            email="test@example.com",
            resume_path=None,
            resume_text=None,
        )
        db_session.add(profile)
        await db_session.flush()

        with pytest.raises(ValueError, match="no resume_path or resume_text"):
            await load_resume_text(resume_path=None, session=db_session)


# ══════════════════════════════════════════════════════════════════════════════
# TestRunAgent — full pipeline tests
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestRunAgent:
    """Tests for run() — the main agent entry point."""

    async def test_scores_new_jobs_and_updates_status(self, db_session: AsyncSession) -> None:
        """
        Full happy-path pipeline: new job → scored → status updated to SCORED.
        Claude API is mocked to avoid real HTTP calls.
        """
        # Arrange: add a UserProfile with cached resume text and a NEW job
        profile = UserProfile(
            full_name="Jane",
            email="jane@test.com",
            resume_text="Python developer with 3 years experience.",
        )
        job = make_job(description="Looking for Python backend engineer.")
        db_session.add_all([profile, job])
        await db_session.flush()

        mock_client = make_mock_claude_client(score=82, reasoning="Good Python match.")

        with patch("agents.resume_match.anthropic.Anthropic", return_value=mock_client):
            result = await run(session=db_session)

        assert result.total_scored == 1
        assert result.total_errors == 0

        # Verify DB was updated
        from sqlalchemy import select
        db_job = (await db_session.execute(select(Job).where(Job.id == job.id))).scalar_one()
        assert db_job.match_score == 82.0
        assert db_job.status == JobStatus.SCORED

    async def test_skips_jobs_with_no_description(self, db_session: AsyncSession) -> None:
        """
        Jobs with empty or null descriptions cannot be scored — agent should
        skip them gracefully and increment total_skipped.
        """
        profile = UserProfile(
            full_name="Jane",
            email="jane@test.com",
            resume_text="Python developer.",
        )
        job = make_job(description="")  # Empty description
        db_session.add_all([profile, job])
        await db_session.flush()

        mock_client = make_mock_claude_client()
        with patch("agents.resume_match.anthropic.Anthropic", return_value=mock_client):
            result = await run(session=db_session)

        assert result.total_skipped == 1
        assert result.total_scored == 0
        # Claude should NOT have been called for this job
        mock_client.messages.create.assert_not_called()

    async def test_returns_early_if_no_new_jobs(self, db_session: AsyncSession) -> None:
        """
        If there are no NEW jobs, the agent should return cleanly with zero counts.
        Claude API should never be called.
        """
        profile = UserProfile(
            full_name="Jane",
            email="jane@test.com",
            resume_text="Python developer.",
        )
        # Add only a SCORED job (already processed)
        job = make_job(status=JobStatus.SCORED)
        db_session.add_all([profile, job])
        await db_session.flush()

        mock_client = make_mock_claude_client()
        with patch("agents.resume_match.anthropic.Anthropic", return_value=mock_client):
            result = await run(session=db_session)

        assert result.total_jobs_fetched == 0
        assert result.total_scored == 0
        mock_client.messages.create.assert_not_called()

    async def test_continues_after_individual_job_failure(self, db_session: AsyncSession) -> None:
        """
        If scoring one job raises an exception, the agent should log the error
        and continue to score the remaining jobs (never crash the whole run).
        """
        profile = UserProfile(
            full_name="Jane",
            email="jane@test.com",
            resume_text="Python developer.",
        )
        failing_job = make_job(title="Failing Job", description="This job will fail.")
        passing_job = make_job(title="Passing Job", description="This job will score.")
        db_session.add_all([profile, failing_job, passing_job])
        await db_session.flush()

        # Make the mock raise on the first call, succeed on the second
        good_response = make_mock_claude_client(score=75).messages.create.return_value

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Simulated API failure")
            return good_response

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = side_effect

        with patch("agents.resume_match.anthropic.Anthropic", return_value=mock_client):
            result = await run(session=db_session)

        # One error, one scored — agent did not abort
        assert result.total_errors == 1
        assert result.total_scored == 1

    async def test_dry_run_does_not_update_db(self, db_session: AsyncSession) -> None:
        """
        In dry_run mode, scores should be printed but NOT written to the database.
        Job status should remain NEW.
        """
        profile = UserProfile(
            full_name="Jane",
            email="jane@test.com",
            resume_text="Python developer.",
        )
        job = make_job(description="Looking for Python backend engineer.")
        db_session.add_all([profile, job])
        await db_session.flush()

        mock_client = make_mock_claude_client(score=90)
        with patch("agents.resume_match.anthropic.Anthropic", return_value=mock_client):
            result = await run(dry_run=True, session=db_session)

        # Result shows 1 scored (printed)
        assert result.total_scored == 1

        # But the DB row should NOT have been updated
        from sqlalchemy import select
        db_job = (await db_session.execute(select(Job).where(Job.id == job.id))).scalar_one()
        assert db_job.match_score is None   # Was not written
        assert db_job.status == JobStatus.NEW  # Still NEW

    async def test_result_counts_match_input(self, db_session: AsyncSession) -> None:
        """
        total_jobs_fetched should equal the number of NEW jobs in the DB at run start.
        """
        profile = UserProfile(
            full_name="Jane",
            email="jane@test.com",
            resume_text="Python developer.",
        )
        jobs = [make_job(description=f"Job description {i}") for i in range(3)]
        db_session.add(profile)
        db_session.add_all(jobs)
        await db_session.flush()

        mock_client = make_mock_claude_client(score=70)
        with patch("agents.resume_match.anthropic.Anthropic", return_value=mock_client):
            result = await run(session=db_session)

        assert result.total_jobs_fetched == 3
        assert result.total_scored == 3
        assert result.total_errors == 0
