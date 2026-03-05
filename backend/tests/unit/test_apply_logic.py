"""
tests/unit/test_apply_logic.py — Unit tests for Apply Agent pure functions.

WHY THESE TESTS ARE FAST AND DEPENDENCY-FREE:
  All functions in apply_logic.py are "pure" — they take plain Python values and
  return plain Python values with no DB calls, no HTTP calls, no filesystem writes
  (except get_screenshots_dir, which we isolate using pytest's `tmp_path` fixture).

  No mocks needed. No async needed. Just call the function and assert the output.
  These tests run in milliseconds.
"""

import uuid
from pathlib import Path

import pytest

from agents.apply_logic import (
    ApplyConfig,
    ApplyResult,
    build_optional_field_map,
    get_screenshots_dir,
    screenshot_filename,
    split_full_name,
)
from models.user_profile import UserProfile


# ── split_full_name ────────────────────────────────────────────────────────────

class TestSplitFullName:
    """Tests for the name-splitting helper used to fill #first_name / #last_name."""

    def test_two_words(self):
        """Standard "First Last" → splits on the space."""
        first, last = split_full_name("Nick Perry")
        assert first == "Nick"
        assert last == "Perry"

    def test_one_word(self):
        """Single-word name (e.g. 'Cher') → last name is empty string."""
        first, last = split_full_name("Cher")
        assert first == "Cher"
        assert last == ""

    def test_multi_word_last_name(self):
        """
        'Mary Jane Watson' → first='Mary', last='Jane Watson'.
        We split on the FIRST space only — everything after belongs to last name.
        This handles hyphenated and multi-word surnames like 'van der Berg'.
        """
        first, last = split_full_name("Mary Jane Watson")
        assert first == "Mary"
        assert last == "Jane Watson"

    def test_leading_trailing_whitespace_stripped(self):
        """Strip outer whitespace before splitting."""
        first, last = split_full_name("  Nick Perry  ")
        assert first == "Nick"

    def test_empty_string(self):
        """Empty input → both parts are empty strings."""
        first, last = split_full_name("")
        assert first == ""
        assert last == ""


# ── screenshot_filename ────────────────────────────────────────────────────────

class TestScreenshotFilename:
    """Tests for the timestamped screenshot filename generator."""

    def test_contains_job_id(self):
        """The job UUID must appear in the filename for traceability."""
        job_id = uuid.UUID("12345678-1234-1234-1234-123456789abc")
        filename = screenshot_filename(job_id)
        assert str(job_id) in filename

    def test_default_suffix_is_form(self):
        """Without a suffix argument, filename ends with '_form.png'."""
        filename = screenshot_filename(uuid.uuid4())
        assert filename.endswith("_form.png")

    def test_custom_suffix_result(self):
        """Passing suffix='result' → filename ends with '_result.png'."""
        filename = screenshot_filename(uuid.uuid4(), suffix="result")
        assert filename.endswith("_result.png")

    def test_png_extension(self):
        """All screenshots are PNG files."""
        filename = screenshot_filename(uuid.uuid4())
        assert filename.endswith(".png")

    def test_timestamp_format(self):
        """
        Filename must start with YYYYMMDD_HHMMSS (15 chars, underscore at position 8).
        Example: "20260304_120000_..."
        WHY: Timestamps let you see when an application was attempted at a glance
        and prevent filename collisions on re-runs.
        """
        filename = screenshot_filename(uuid.uuid4())
        timestamp_part = filename[:15]
        # 8 digits (date) + underscore + 6 digits (time) = 15 chars
        assert len(timestamp_part) == 15
        assert timestamp_part[8] == "_"   # Separator between date and time
        assert timestamp_part[:8].isdigit()
        assert timestamp_part[9:].isdigit()

    def test_unique_per_call(self):
        """Two calls with the same job_id should produce distinct filenames if the time differs."""
        # We can't guarantee the clock advances between two immediate calls,
        # but we can verify the format is consistent and includes the job_id.
        job_id = uuid.uuid4()
        f1 = screenshot_filename(job_id, "form")
        f2 = screenshot_filename(job_id, "result")
        # Different suffix → always different
        assert f1 != f2


# ── get_screenshots_dir ────────────────────────────────────────────────────────

class TestGetScreenshotsDir:
    """Tests for the directory-creation helper."""

    def test_creates_directory(self, tmp_path):
        """If the target directory doesn't exist, it should be created."""
        target = tmp_path / "screenshots" / "nested"
        config = ApplyConfig(screenshots_dir=str(target))
        result = get_screenshots_dir(config)
        assert result.exists()
        assert result.is_dir()

    def test_returns_path_object(self, tmp_path):
        """Return type should be pathlib.Path, not str."""
        config = ApplyConfig(screenshots_dir=str(tmp_path / "shots"))
        result = get_screenshots_dir(config)
        assert isinstance(result, Path)

    def test_returns_correct_path(self, tmp_path):
        """Returned path should point to the configured directory."""
        target = tmp_path / "my_screenshots"
        config = ApplyConfig(screenshots_dir=str(target))
        result = get_screenshots_dir(config)
        assert result == target

    def test_idempotent_if_directory_exists(self, tmp_path):
        """Calling twice on an existing directory should not raise any error."""
        config = ApplyConfig(screenshots_dir=str(tmp_path))
        get_screenshots_dir(config)   # First call — creates
        get_screenshots_dir(config)   # Second call — already exists, should be fine


# ── build_optional_field_map ───────────────────────────────────────────────────

class TestBuildOptionalFieldMap:
    """
    Tests for the CSS selector map used to fill optional Greenhouse fields.

    WHY THESE FIELDS ARE OPTIONAL:
      Greenhouse lets each company customize their application form. Some add
      LinkedIn and GitHub fields; others don't. We only include selectors for
      fields the user has filled in, and we wrap each fill() in a try/except in
      apply.py so missing fields don't crash the agent.
    """

    def _make_profile(self, **kwargs) -> UserProfile:
        """Helper: create a minimal UserProfile with specified optional fields."""
        return UserProfile(
            full_name="Test User",
            email="test@test.com",
            **kwargs,
        )

    def test_empty_when_no_optional_fields(self):
        """If the profile has no optional URLs, the map should be empty."""
        profile = self._make_profile(
            linkedin_url=None,
            github_url=None,
            portfolio_url=None,
        )
        assert build_optional_field_map(profile) == []

    def test_linkedin_included_when_set(self):
        """LinkedIn URL → at least one entry with a 'linkedin' selector."""
        profile = self._make_profile(linkedin_url="https://linkedin.com/in/nick")
        field_map = build_optional_field_map(profile)
        selectors = [sel for sel, _ in field_map]
        assert any("linkedin" in sel for sel in selectors)

    def test_github_included_when_set(self):
        """GitHub URL → at least one entry with a 'github' selector."""
        profile = self._make_profile(github_url="https://github.com/nickperry")
        field_map = build_optional_field_map(profile)
        selectors = [sel for sel, _ in field_map]
        assert any("github" in sel for sel in selectors)

    def test_portfolio_included_when_set(self):
        """Portfolio URL → entries for both 'portfolio' and 'website' selectors."""
        profile = self._make_profile(portfolio_url="https://nick.dev")
        field_map = build_optional_field_map(profile)
        selectors = [sel for sel, _ in field_map]
        assert any("portfolio" in sel or "website" in sel for sel in selectors)

    def test_github_absent_when_not_set(self):
        """If github_url is None, no github entry should appear in the map."""
        profile = self._make_profile(
            linkedin_url="https://linkedin.com/in/nick",
            github_url=None,
        )
        field_map = build_optional_field_map(profile)
        selectors = [sel for sel, _ in field_map]
        assert not any("github" in sel for sel in selectors)

    def test_values_are_correct(self):
        """The value in each (selector, value) tuple must match the profile field."""
        linkedin = "https://linkedin.com/in/nick"
        profile = self._make_profile(linkedin_url=linkedin)
        field_map = build_optional_field_map(profile)
        values = [val for _, val in field_map]
        assert linkedin in values

    def test_all_fields_populated(self):
        """When all optional fields are set, the map should contain entries for all."""
        profile = self._make_profile(
            linkedin_url="https://linkedin.com/in/test",
            github_url="https://github.com/test",
            portfolio_url="https://test.dev",
        )
        field_map = build_optional_field_map(profile)
        selectors = [sel for sel, _ in field_map]
        assert any("linkedin" in sel for sel in selectors)
        assert any("github" in sel for sel in selectors)
        assert any("portfolio" in sel or "website" in sel for sel in selectors)


# ── ApplyConfig defaults ───────────────────────────────────────────────────────

class TestApplyConfigDefaults:
    """
    Verify that ApplyConfig defaults match the values documented in config.py.

    WHY TEST DEFAULTS:
      The default values in ApplyConfig are the "production safe" settings.
      If someone changes them accidentally, these tests catch it before it
      causes unintended live submissions (dry_run=False by default is correct
      but must be intentional — changing it to True by default would break prod).
    """

    def test_headless_default(self):
        assert ApplyConfig().headless is True

    def test_dry_run_default(self):
        """dry_run must default to False — we want to actually apply in production."""
        assert ApplyConfig().dry_run is False

    def test_min_score_default(self):
        """Default min_score of 70 matches APPLY_MIN_SCORE in config.py."""
        assert ApplyConfig().min_score == 70.0

    def test_screenshots_dir_default(self):
        assert ApplyConfig().screenshots_dir == "data/screenshots"

    def test_page_timeout_ms_default(self):
        """30 seconds is a reasonable timeout for slow job board pages."""
        assert ApplyConfig().page_timeout_ms == 30_000


# ── ApplyResult defaults ───────────────────────────────────────────────────────

class TestApplyResult:
    """ApplyResult should initialize with all counters at zero."""

    def test_all_counters_start_at_zero(self):
        result = ApplyResult()
        assert result.total_attempted == 0
        assert result.total_applied == 0
        assert result.total_dry_run == 0
        assert result.total_failed == 0
        assert result.total_skipped == 0

    def test_errors_starts_empty(self):
        result = ApplyResult()
        assert result.errors == []

    def test_errors_is_not_shared_between_instances(self):
        """
        Each ApplyResult must have its OWN errors list.
        WHY: A mutable default like `errors = []` in a dataclass is shared between
        instances unless you use `field(default_factory=list)`.
        This test catches that mistake.
        """
        r1 = ApplyResult()
        r2 = ApplyResult()
        r1.errors.append("oops")
        assert r2.errors == []
