"""
tests/unit/test_models.py — Unit tests for database models.

WHAT WE'RE TESTING HERE:
  - That Job, Application, UserProfile objects can be constructed correctly
  - That default values work as expected
  - That enum values are valid
  - That __repr__ doesn't crash

WHAT WE'RE NOT TESTING HERE:
  - DB writes (that's in integration tests)
  - Unique constraints (those require a real DB)

WHY TEST MODEL CONSTRUCTION?
  If you misspell a column name or give it the wrong type, Python won't tell you
  until runtime — potentially in production. These tests catch model-level bugs early.
"""

import uuid

import pytest

from models.job import Job, JobSource, JobStatus
from models.application import Application, ApplicationStatus
from models.user_profile import UserProfile


class TestJobModel:
    """Tests for the Job model."""

    def test_job_default_status_is_new(self):
        """
        Verify a new Job defaults to JobStatus.NEW.
        The Scraper Agent creates jobs — they should start in the NEW state.
        """
        job = Job(
            title="Software Engineer Intern",
            company="Acme Corp",
            source_url="https://example.com/jobs/123",
            source=JobSource.LINKEDIN,
        )
        assert job.status == JobStatus.NEW

    def test_job_uuid_is_set_automatically(self):
        """
        Verify that UUID is auto-generated when creating a Job.
        This tests our default=uuid.uuid4 configuration.
        """
        job = Job(
            title="Backend Engineer",
            company="StartupXYZ",
            source_url="https://example.com/jobs/456",
            source=JobSource.INDEED,
        )
        assert job.id is not None
        assert isinstance(job.id, uuid.UUID)

    def test_two_jobs_have_different_uuids(self):
        """
        Each job must have a unique ID. This would break if uuid4() always returned
        the same value (it shouldn't, but good to test the wiring).
        """
        job1 = Job(title="SWE", company="A", source_url="https://a.com", source=JobSource.LINKEDIN)
        job2 = Job(title="SWE", company="B", source_url="https://b.com", source=JobSource.INDEED)
        assert job1.id != job2.id

    def test_job_match_score_defaults_to_none(self):
        """
        match_score should be None until the Resume Match Agent processes the job.
        A non-null default would be misleading (it hasn't been scored yet).
        """
        job = Job(
            title="ML Engineer",
            company="AI Corp",
            source_url="https://ai.com/jobs/1",
            source=JobSource.OTHER,
        )
        assert job.match_score is None

    def test_job_repr_contains_title_and_company(self):
        """
        __repr__ should include enough info to identify a job at a glance in logs.
        Tests that __repr__ runs without raising an exception.
        """
        job = Job(
            title="Senior SWE",
            company="Google",
            source_url="https://google.com/jobs/99",
            source=JobSource.LINKEDIN,
        )
        repr_str = repr(job)
        assert "Senior SWE" in repr_str
        assert "Google" in repr_str

    def test_job_status_enum_values(self):
        """All JobStatus enum values should be lowercase strings for clean DB storage."""
        assert JobStatus.NEW == "new"
        assert JobStatus.APPLIED == "applied"
        assert JobStatus.IGNORED == "ignored"

    def test_job_source_enum_values(self):
        """All JobSource enum values should be lowercase strings."""
        assert JobSource.LINKEDIN == "linkedin"
        assert JobSource.INDEED == "indeed"


class TestApplicationModel:
    """Tests for the Application model."""

    def test_application_default_status_is_pending(self):
        """
        A newly created application should be in PENDING state.
        The Apply Agent sets it to IN_PROGRESS when it starts working.
        """
        app = Application(
            job_id=uuid.uuid4(),
        )
        assert app.status == ApplicationStatus.PENDING

    def test_application_uuid_auto_generated(self):
        """Application gets a UUID just like Job."""
        app = Application(job_id=uuid.uuid4())
        assert app.id is not None
        assert isinstance(app.id, uuid.UUID)

    def test_application_applied_at_defaults_to_none(self):
        """
        applied_at should be None until the Apply Agent successfully submits.
        A non-null default would be lying about when the application happened.
        """
        app = Application(job_id=uuid.uuid4())
        assert app.applied_at is None

    def test_application_repr(self):
        """__repr__ should not raise and should include status."""
        job_id = uuid.uuid4()
        app = Application(job_id=job_id, status=ApplicationStatus.SUBMITTED)
        repr_str = repr(app)
        assert "submitted" in repr_str


class TestUserProfileModel:
    """Tests for the UserProfile model."""

    def test_user_profile_default_values(self):
        """
        UserProfile has sensible defaults for all optional fields.
        A freshly created profile should be safe to use even without filling everything in.
        """
        profile = UserProfile(
            full_name="Nick Perry",
            email="nick@example.com",
        )
        assert profile.full_name == "Nick Perry"
        assert profile.email == "nick@example.com"
        assert profile.phone is None
        assert profile.resume_path is None
        assert profile.target_internships is True
        assert profile.target_new_grad is True

    def test_user_profile_uuid_auto_generated(self):
        """UserProfile gets a UUID just like other models."""
        profile = UserProfile(full_name="Test User", email="test@example.com")
        assert profile.id is not None
        assert isinstance(profile.id, uuid.UUID)
