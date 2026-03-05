"""
agents/apply_logic.py — Pure functions for the Apply Agent.

ARCHITECTURE (Functional Core / Imperative Shell):
  This file is the "Functional Core" — all pure functions with no I/O or side effects.
  - ApplyConfig:              configuration dataclass
  - ApplyResult:              result summary dataclass
  - split_full_name:          splits "Nick Perry" → ("Nick", "Perry")
  - get_screenshots_dir:      creates and returns the screenshots directory
  - screenshot_filename:      generates a timestamped filename for a screenshot
  - build_optional_field_map: returns CSS selectors + values for optional Greenhouse fields

WHY PURE FUNCTIONS:
  Same reason as resume_match_logic.py — pure functions need zero mocking in tests.
  You call them with test data and assert the output. Instant, zero dependencies.

  The "Imperative Shell" (apply.py) handles DB queries, Playwright automation, and file
  I/O — those functions are tested with mocks.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from models.user_profile import UserProfile


# ── Configuration ─────────────────────────────────────────────────────────────

@dataclass
class ApplyConfig:
    """
    Configuration for one Apply Agent run.

    Fields:
        headless:              Run Playwright's Chromium invisibly (prod) or visibly (debug).
        dry_run:               Fill the form but never click submit — safe on live job boards.
        handoff:               Fill the form in a visible browser and wait for the user to
                               complete any remaining fields and submit manually. Overrides
                               dry_run (handoff always uses a visible browser).
        handoff_wait_seconds:  How long to keep the browser open in handoff mode before
                               moving to the next job. Default 300 = 5 minutes.
        min_score:             Skip jobs below this match_score.
        screenshots_dir:       Directory to save Playwright screenshots for audit trail.
        page_timeout_ms:       Milliseconds to wait for each page navigation before giving up.

    WHY A DATACLASS OVER HARDCODING:
      Same reason as MatchConfig in resume_match_logic.py — lets tests inject custom
      configs (e.g. dry_run=True, screenshots_dir=tmpdir) without monkey-patching
      global settings or source files.
    """

    headless: bool = True
    dry_run: bool = False
    handoff: bool = False
    handoff_wait_seconds: int = 300   # 5 minutes for user to complete and submit
    min_score: float = 70.0
    screenshots_dir: str = "data/screenshots"
    page_timeout_ms: int = 30_000   # 30 seconds per page navigation


# ── Result Summary ─────────────────────────────────────────────────────────────

@dataclass
class ApplyResult:
    """
    Summary of what happened during one Apply Agent run. Returned by run().

    Fields:
        total_attempted: Jobs where the browser was opened and form filling started.
        total_applied:   Jobs where the form was submitted successfully.
        total_dry_run:   Jobs where the form was filled but submit was skipped (dry_run).
        total_failed:    Jobs where an unhandled exception stopped the apply attempt.
        total_skipped:   REVIEWED jobs skipped because match_score < min_score.
        errors:          Human-readable error messages for each failure.

    WHY SEPARATE total_dry_run FROM total_applied:
      Dry-run jobs didn't actually submit, so they shouldn't count toward "applied".
      Keeping them separate lets the caller report accurately on what happened.
    """

    total_attempted: int = 0
    total_applied: int = 0
    total_dry_run: int = 0
    total_failed: int = 0
    total_skipped: int = 0
    errors: list[str] = field(default_factory=list)


# ── Name Splitting ─────────────────────────────────────────────────────────────

def split_full_name(full_name: str) -> tuple[str, str]:
    """
    Split a full name string into first and last name for form fields.

    STRATEGY: Split on the first space only. Everything before the first space is
    the first name; everything after (including further spaces) is the last name.

    WHY: Greenhouse forms have separate #first_name and #last_name fields.
    Our UserProfile stores a single `full_name`. This bridges the gap without
    requiring users to fill in two separate fields in the settings UI.

    Examples:
        "Nick Perry"       → ("Nick", "Perry")
        "Mary Jane Watson" → ("Mary", "Jane Watson")   # multi-word last name
        "Cher"             → ("Cher", "")              # single-word name

    Args:
        full_name: The user's full name from UserProfile, e.g. "Nick Perry".

    Returns:
        (first_name, last_name) tuple. Both values are stripped of leading/trailing
        whitespace. last_name is "" for single-word names.
    """
    parts = full_name.strip().split(" ", 1)  # Split at first space only
    first = parts[0] if parts else ""
    last = parts[1] if len(parts) > 1 else ""
    return first, last


# ── Screenshots ────────────────────────────────────────────────────────────────

def get_screenshots_dir(config: ApplyConfig) -> Path:
    """
    Ensure the screenshots directory exists and return it as a Path.

    WHY mkdir HERE:
      Keeping filesystem side effects in one function makes it easy to test —
      tests pass a tmpdir in ApplyConfig.screenshots_dir, this function creates
      it (idempotent: `exist_ok=True` means no error if it already exists).

    Args:
        config: ApplyConfig with screenshots_dir field.

    Returns:
        pathlib.Path pointing to the (now-created) screenshots directory.
    """
    path = Path(config.screenshots_dir)
    # parents=True: create intermediate directories (e.g. data/ before data/screenshots/).
    # exist_ok=True: no error if the directory already exists — safe to call multiple times.
    path.mkdir(parents=True, exist_ok=True)
    return path


def screenshot_filename(job_id: uuid.UUID, suffix: str = "form") -> str:
    """
    Generate a timestamped, unique filename for a Playwright screenshot.

    FORMAT: "YYYYMMDD_HHMMSS_{job_id}_{suffix}.png"
    EXAMPLE: "20260304_120000_a1b2c3d4-1234-..._form.png"

    WHY TIMESTAMP + JOB ID:
      If we apply to the same job multiple times (retry after failure), we don't
      want the second screenshot to overwrite the first. Timestamp + UUID makes
      every filename unique without needing a counter or DB lookup.

    WHY TWO SUFFIXES ("form" and "result"):
      We always screenshot the completed form before submitting (for audit + debugging).
      If not a dry run, we screenshot the result/confirmation page after submission.
      The suffix tells them apart in the directory.

    Args:
        job_id: The UUID of the Job being applied to.
        suffix: "form" (completed form, pre-submit) or "result" (post-submission page).

    Returns:
        Filename string only — not a full path. Caller joins with get_screenshots_dir().
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{timestamp}_{job_id}_{suffix}.png"


# ── Optional Field Mapping ─────────────────────────────────────────────────────

def build_optional_field_map(profile: UserProfile) -> list[tuple[str, str]]:
    """
    Build a list of (CSS selector, value) pairs for optional Greenhouse form fields.

    WHY OPTIONAL FIELDS NEED SPECIAL HANDLING:
      Greenhouse is a customizable ATS — each company configures their own form.
      Some companies add a LinkedIn field; others don't. If we blindly try to fill
      a field that doesn't exist on the form, Playwright raises a TimeoutError.

      Our strategy:
        1. Only include fields where the profile has an actual value.
        2. Use broad attribute selectors (input[id*='linkedin']) that match
           naming variations: "linkedin", "linkedin_url", "applicant_linkedin", etc.
        3. Wrap each fill() in a try/except in apply.py — skip silently if absent.

    SELECTOR STRATEGY — `[id*='linkedin']`:
      The `*=` operator means "id contains this substring". This is more robust
      than an exact match like `[id='linkedin_url']` which would miss "applicant_linkedin".

    Args:
        profile: UserProfile loaded from the DB.

    Returns:
        List of (selector, value) tuples. Only includes fields where profile has a value.
        NOTE: portfolio_url generates TWO entries (trying both common selector names).
    """
    fields: list[tuple[str, str]] = []

    # LinkedIn — present on most Greenhouse forms
    if profile.linkedin_url:
        fields.append(("input[id*='linkedin']", profile.linkedin_url))

    # GitHub — common on tech-focused engineering forms
    if profile.github_url:
        fields.append(("input[id*='github']", profile.github_url))

    # Portfolio / personal website — companies use different field names
    # We try both selectors so we cover more forms without crashing on either.
    if profile.portfolio_url:
        fields.append(("input[id*='portfolio']", profile.portfolio_url))
        fields.append(("input[id*='website']", profile.portfolio_url))

    return fields
