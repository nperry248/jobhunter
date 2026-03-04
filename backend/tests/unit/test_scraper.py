"""
tests/unit/test_scraper.py — Unit tests for scraper parsing and filtering logic.

WHAT WE'RE TESTING:
  - parse_greenhouse_response(): correct field extraction, handles missing/malformed data
  - parse_lever_response(): correct field extraction, handles missing data
  - passes_filters(): every filter type independently and in combination

WHAT WE'RE NOT TESTING HERE:
  - HTTP calls (those are mocked in integration tests)
  - Database writes (those are in integration tests)

WHY PURE UNIT TESTS MATTER:
  These tests run in <100ms and need no external services (no Docker, no network).
  You can run them on every file save to catch regressions instantly.
"""

import pytest

from agents.scraper_parsers import (
    ParsedJob,
    ScraperFilters,
    parse_greenhouse_response,
    parse_lever_response,
    passes_filters,
)
from models.job import JobSource


# ── Fixtures — reusable test data ─────────────────────────────────────────────
# CONCEPT — pytest fixtures:
#   Instead of repeating setup in every test, we define fixtures once.
#   Tests declare what they need as function arguments; pytest injects them.

@pytest.fixture
def greenhouse_job_dict():
    """A minimal but complete Greenhouse API job dict."""
    return {
        "id": 12345,
        "title": "Software Engineer Intern",
        "absolute_url": "https://boards.greenhouse.io/testco/jobs/12345",
        "location": {"name": "San Francisco, CA"},
        "content": "<p>Come build cool things.</p>",
    }


@pytest.fixture
def lever_job_dict():
    """A minimal but complete Lever API job posting dict."""
    return {
        "id": "abc-uuid-123",
        "text": "Software Engineer Intern",
        "hostedUrl": "https://jobs.lever.co/testco/abc-uuid-123",
        "categories": {
            "location": "New York, NY",
            "commitment": "Full-time",
        },
        "descriptionPlain": "Come build cool things.",
    }


@pytest.fixture
def basic_filters():
    """Filters with no active criteria — everything passes."""
    return ScraperFilters()


# ── Greenhouse Parser Tests ────────────────────────────────────────────────────

class TestParseGreenhouseResponse:

    def test_parses_title_correctly(self, greenhouse_job_dict):
        """Title should be extracted from the 'title' key."""
        result = parse_greenhouse_response({"jobs": [greenhouse_job_dict]}, "Test Co")
        assert len(result) == 1
        assert result[0].title == "Software Engineer Intern"

    def test_parses_company_name(self, greenhouse_job_dict):
        """Company name comes from our argument, not the API response (Greenhouse doesn't include it)."""
        result = parse_greenhouse_response({"jobs": [greenhouse_job_dict]}, "Acme Corp")
        assert result[0].company == "Acme Corp"

    def test_parses_source_url(self, greenhouse_job_dict):
        """absolute_url should become the source_url on the ParsedJob."""
        result = parse_greenhouse_response({"jobs": [greenhouse_job_dict]}, "Test Co")
        assert result[0].source_url == "https://boards.greenhouse.io/testco/jobs/12345"

    def test_parses_location(self, greenhouse_job_dict):
        """Location is nested in {'name': '...'} — we extract the string."""
        result = parse_greenhouse_response({"jobs": [greenhouse_job_dict]}, "Test Co")
        assert result[0].location == "San Francisco, CA"

    def test_source_is_greenhouse(self, greenhouse_job_dict):
        """All jobs from this parser should have source=GREENHOUSE."""
        result = parse_greenhouse_response({"jobs": [greenhouse_job_dict]}, "Test Co")
        assert result[0].source == JobSource.GREENHOUSE

    def test_parses_description(self, greenhouse_job_dict):
        """Description from 'content' field should be stored."""
        result = parse_greenhouse_response({"jobs": [greenhouse_job_dict]}, "Test Co")
        assert result[0].description == "<p>Come build cool things.</p>"

    def test_missing_location_returns_none(self, greenhouse_job_dict):
        """Jobs with no location field should have location=None, not crash."""
        greenhouse_job_dict.pop("location")
        result = parse_greenhouse_response({"jobs": [greenhouse_job_dict]}, "Test Co")
        assert result[0].location is None

    def test_missing_title_is_skipped(self, greenhouse_job_dict):
        """A job with no title should be silently skipped (not added to results)."""
        greenhouse_job_dict["title"] = ""
        result = parse_greenhouse_response({"jobs": [greenhouse_job_dict]}, "Test Co")
        assert len(result) == 0

    def test_missing_url_is_skipped(self, greenhouse_job_dict):
        """A job with no absolute_url should be silently skipped."""
        greenhouse_job_dict["absolute_url"] = ""
        result = parse_greenhouse_response({"jobs": [greenhouse_job_dict]}, "Test Co")
        assert len(result) == 0

    def test_empty_jobs_list_returns_empty(self):
        """An API response with no jobs should return an empty list, not crash."""
        result = parse_greenhouse_response({"jobs": []}, "Test Co")
        assert result == []

    def test_missing_jobs_key_returns_empty(self):
        """If the response is missing the 'jobs' key entirely, return empty list."""
        result = parse_greenhouse_response({}, "Test Co")
        assert result == []

    def test_malformed_job_is_skipped_not_crash(self):
        """A completely malformed job dict should be skipped without crashing the whole parse."""
        data = {"jobs": [None, {"title": "Valid Job", "absolute_url": "https://example.com"}]}
        # Should skip None entry and parse the valid one
        result = parse_greenhouse_response(data, "Test Co")
        assert len(result) == 1
        assert result[0].title == "Valid Job"

    def test_parses_multiple_jobs(self, greenhouse_job_dict):
        """Multiple jobs in the response should all be parsed."""
        job2 = {**greenhouse_job_dict, "id": 99999, "title": "Backend Engineer"}
        result = parse_greenhouse_response(
            {"jobs": [greenhouse_job_dict, job2]}, "Test Co"
        )
        assert len(result) == 2
        titles = {j.title for j in result}
        assert "Software Engineer Intern" in titles
        assert "Backend Engineer" in titles


# ── Lever Parser Tests ────────────────────────────────────────────────────────

class TestParseLeverResponse:

    def test_parses_title_from_text_field(self, lever_job_dict):
        """Lever uses 'text' for the job title (not 'title' like Greenhouse)."""
        result = parse_lever_response([lever_job_dict], "Test Co")
        assert result[0].title == "Software Engineer Intern"

    def test_parses_location_from_categories(self, lever_job_dict):
        """Location is nested in categories.location."""
        result = parse_lever_response([lever_job_dict], "Test Co")
        assert result[0].location == "New York, NY"

    def test_source_is_lever(self, lever_job_dict):
        """All jobs from this parser should have source=LEVER."""
        result = parse_lever_response([lever_job_dict], "Test Co")
        assert result[0].source == JobSource.LEVER

    def test_external_id_is_lever_uuid(self, lever_job_dict):
        """External ID should be the Lever posting UUID."""
        result = parse_lever_response([lever_job_dict], "Test Co")
        assert result[0].external_id == "abc-uuid-123"

    def test_empty_list_returns_empty(self):
        """Empty response list returns empty result list."""
        result = parse_lever_response([], "Test Co")
        assert result == []

    def test_missing_title_is_skipped(self, lever_job_dict):
        """Job with empty 'text' field should be skipped."""
        lever_job_dict["text"] = ""
        result = parse_lever_response([lever_job_dict], "Test Co")
        assert len(result) == 0


# ── Filter Logic Tests ────────────────────────────────────────────────────────

def make_job(title="Software Engineer", company="Acme Corp", location="San Francisco, CA"):
    """Helper: create a ParsedJob with sensible defaults for filter tests."""
    return ParsedJob(
        title=title,
        company=company,
        source_url=f"https://example.com/{title.lower().replace(' ', '-')}",
        source=JobSource.GREENHOUSE,
        location=location,
    )


class TestPassesFilters:

    def test_empty_filters_passes_everything(self):
        """With no filters set, every job should pass."""
        job = make_job()
        assert passes_filters(job, ScraperFilters()) is True

    # ── Job type filter ───────────────────────────────────────────────────────

    def test_internship_filter_passes_intern_title(self):
        """A job titled 'SWE Intern' should pass the internship filter."""
        job = make_job(title="Software Engineer Intern")
        filters = ScraperFilters(job_type="internship")
        assert passes_filters(job, filters) is True

    def test_internship_filter_passes_internship_title(self):
        """'Software Engineering Internship' should pass — 'internship' is in title."""
        job = make_job(title="Software Engineering Internship")
        filters = ScraperFilters(job_type="internship")
        assert passes_filters(job, filters) is True

    def test_internship_filter_rejects_senior_role(self):
        """'Senior Software Engineer' has no intern keyword — should be rejected."""
        job = make_job(title="Senior Software Engineer")
        filters = ScraperFilters(job_type="internship")
        assert passes_filters(job, filters) is False

    def test_internship_filter_case_insensitive(self):
        """Filter should be case-insensitive: 'INTERN' matches 'internship' keyword."""
        job = make_job(title="SOFTWARE ENGINEER INTERN")
        filters = ScraperFilters(job_type="internship")
        assert passes_filters(job, filters) is True

    def test_new_grad_filter_passes_entry_level(self):
        """'Entry Level Software Engineer' should pass the new_grad filter."""
        job = make_job(title="Entry Level Software Engineer")
        filters = ScraperFilters(job_type="new_grad")
        assert passes_filters(job, filters) is True

    def test_new_grad_filter_passes_new_grad_title(self):
        """'New Grad Software Engineer' should pass."""
        job = make_job(title="New Grad Software Engineer")
        filters = ScraperFilters(job_type="new_grad")
        assert passes_filters(job, filters) is True

    def test_new_grad_filter_rejects_staff_engineer(self):
        """'Staff Engineer' is senior — should fail the new_grad filter."""
        job = make_job(title="Staff Engineer")
        filters = ScraperFilters(job_type="new_grad")
        assert passes_filters(job, filters) is False

    def test_any_job_type_passes_all(self):
        """'any' job_type should pass senior, intern, and new grad titles."""
        filters = ScraperFilters(job_type="any")
        assert passes_filters(make_job(title="Senior Staff Engineer"), filters) is True
        assert passes_filters(make_job(title="Software Engineer Intern"), filters) is True
        assert passes_filters(make_job(title="New Grad SWE"), filters) is True

    # ── Keyword filter ────────────────────────────────────────────────────────

    def test_keyword_filter_passes_matching_title(self):
        """Title containing a keyword should pass."""
        job = make_job(title="Backend Software Engineer")
        filters = ScraperFilters(keywords=["backend"])
        assert passes_filters(job, filters) is True

    def test_keyword_filter_rejects_non_matching_title(self):
        """Title with no matching keyword should be rejected."""
        job = make_job(title="Product Designer")
        filters = ScraperFilters(keywords=["software engineer", "backend"])
        assert passes_filters(job, filters) is False

    def test_keyword_filter_any_match_passes(self):
        """If ANY keyword matches, the job passes (OR logic, not AND)."""
        job = make_job(title="Frontend Engineer")
        filters = ScraperFilters(keywords=["backend", "frontend", "fullstack"])
        assert passes_filters(job, filters) is True

    def test_keyword_filter_case_insensitive(self):
        """Keyword matching should be case-insensitive."""
        job = make_job(title="BACKEND ENGINEER")
        filters = ScraperFilters(keywords=["backend"])
        assert passes_filters(job, filters) is True

    def test_empty_keywords_passes_all(self):
        """Empty keywords list = no keyword filter = all titles pass."""
        job = make_job(title="Anything Goes Here")
        filters = ScraperFilters(keywords=[])
        assert passes_filters(job, filters) is True

    # ── Company blocklist ─────────────────────────────────────────────────────

    def test_blocklist_rejects_blocked_company(self):
        """A company in the blocklist should be rejected."""
        job = make_job(company="Evil Corp")
        filters = ScraperFilters(company_blocklist=["Evil Corp"])
        assert passes_filters(job, filters) is False

    def test_blocklist_case_insensitive(self):
        """Blocklist check should be case-insensitive."""
        job = make_job(company="evil corp")
        filters = ScraperFilters(company_blocklist=["Evil Corp"])
        assert passes_filters(job, filters) is False

    def test_blocklist_allows_non_blocked_company(self):
        """A company NOT in the blocklist should pass."""
        job = make_job(company="Good Corp")
        filters = ScraperFilters(company_blocklist=["Evil Corp"])
        assert passes_filters(job, filters) is True

    # ── Location filter ───────────────────────────────────────────────────────

    def test_location_filter_passes_matching_location(self):
        """A job in San Francisco should pass a 'san francisco' location filter."""
        job = make_job(location="San Francisco, CA")
        filters = ScraperFilters(locations=["san francisco"])
        assert passes_filters(job, filters) is True

    def test_location_filter_rejects_wrong_location(self):
        """A job in New York should fail a 'san francisco' filter."""
        job = make_job(location="New York, NY")
        filters = ScraperFilters(locations=["san francisco"])
        assert passes_filters(job, filters) is False

    def test_location_filter_passes_remote(self):
        """A 'Remote' job should pass a 'remote' location filter."""
        job = make_job(location="Remote")
        filters = ScraperFilters(locations=["remote"])
        assert passes_filters(job, filters) is True

    def test_location_filter_passes_job_with_no_location(self):
        """If a job has no location listed, it passes location filters (could be remote)."""
        job = make_job(location=None)
        filters = ScraperFilters(locations=["san francisco"])
        assert passes_filters(job, filters) is True

    def test_empty_locations_passes_all(self):
        """Empty locations list = no location filter = all locations pass."""
        job = make_job(location="Antarctica")
        filters = ScraperFilters(locations=[])
        assert passes_filters(job, filters) is True

    # ── Combined filters ──────────────────────────────────────────────────────

    def test_all_filters_must_pass(self):
        """
        All active filters are AND'd — a job must pass ALL of them.
        This test sets internship + keyword filter and verifies both must match.
        """
        intern_backend = make_job(title="Backend Engineer Intern", location="San Francisco, CA")
        filters = ScraperFilters(
            job_type="internship",
            keywords=["backend"],
            locations=["san francisco"],
        )
        assert passes_filters(intern_backend, filters) is True

    def test_fails_if_any_filter_fails(self):
        """
        If even one filter fails, the job is rejected.
        Here the keyword matches but the job type doesn't.
        """
        senior_backend = make_job(title="Senior Backend Engineer")
        filters = ScraperFilters(
            job_type="internship",  # fails — no "intern" in title
            keywords=["backend"],   # would pass
        )
        assert passes_filters(senior_backend, filters) is False

    # ── Senior title filter ───────────────────────────────────────────────────

    def test_exclude_senior_blocks_senior_title(self):
        """'Senior Software Engineer' should be rejected when exclude_senior=True."""
        job = make_job(title="Senior Software Engineer")
        filters = ScraperFilters(exclude_senior=True)
        assert passes_filters(job, filters) is False

    def test_exclude_senior_blocks_staff(self):
        """'Staff Engineer' should be rejected when exclude_senior=True."""
        job = make_job(title="Staff Engineer")
        filters = ScraperFilters(exclude_senior=True)
        assert passes_filters(job, filters) is False

    def test_exclude_senior_blocks_principal(self):
        """'Principal Software Engineer' should be rejected."""
        job = make_job(title="Principal Software Engineer")
        filters = ScraperFilters(exclude_senior=True)
        assert passes_filters(job, filters) is False

    def test_exclude_senior_blocks_manager(self):
        """'Engineering Manager' should be rejected."""
        job = make_job(title="Engineering Manager, Platform")
        filters = ScraperFilters(exclude_senior=True)
        assert passes_filters(job, filters) is False

    def test_exclude_senior_blocks_director(self):
        """'Director of Engineering' should be rejected."""
        job = make_job(title="Director of Software Engineering")
        filters = ScraperFilters(exclude_senior=True)
        assert passes_filters(job, filters) is False

    def test_exclude_senior_passes_plain_swe(self):
        """'Software Engineer' (no seniority) should pass when exclude_senior=True."""
        job = make_job(title="Software Engineer")
        filters = ScraperFilters(exclude_senior=True)
        assert passes_filters(job, filters) is True

    def test_exclude_senior_passes_intern(self):
        """Intern roles should pass even when exclude_senior=True."""
        job = make_job(title="Software Engineer Intern")
        filters = ScraperFilters(exclude_senior=True)
        assert passes_filters(job, filters) is True

    def test_exclude_senior_false_allows_senior(self):
        """When exclude_senior=False (default), senior titles pass through."""
        job = make_job(title="Senior Software Engineer")
        filters = ScraperFilters(exclude_senior=False)
        assert passes_filters(job, filters) is True

    def test_exclude_senior_case_insensitive(self):
        """Senior keyword check should be case-insensitive."""
        job = make_job(title="SENIOR Software Engineer")
        filters = ScraperFilters(exclude_senior=True)
        assert passes_filters(job, filters) is False
