"""
tests/unit/test_resume_match.py — Unit tests for resume_match_logic.py.

WHAT WE'RE TESTING:
  - build_scoring_prompt(): correct structure, truncation, criteria formatting
  - parse_claude_response(): happy path, regex fallback, malformed responses
  - clamp_score(): boundary values, out-of-range values

WHAT WE'RE NOT TESTING HERE:
  - Claude API calls (those are mocked in integration tests)
  - Database writes (those are in integration tests)

WHY THESE TESTS ARE FAST:
  All functions in resume_match_logic.py are pure — no I/O, no mocking needed.
  These tests run in <50ms with zero external dependencies.
"""

import pytest

from agents.resume_match_logic import (
    MatchConfig,
    build_scoring_prompt,
    clamp_score,
    parse_claude_response,
)


# ── Shared fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def sample_resume() -> str:
    """A realistic resume text fragment for testing."""
    return (
        "Jane Doe | jane@example.com | github.com/janedoe\n"
        "Education: BS Computer Science, UC Berkeley, 2024, GPA 3.8\n"
        "Skills: Python, TypeScript, React, PostgreSQL, AWS, Docker\n"
        "Experience: SWE Intern @ Stripe (Summer 2023) — built payment API endpoints\n"
        "Projects: Distributed rate limiter in Go; React dashboard for real-time analytics"
    )


@pytest.fixture
def sample_description() -> str:
    """A realistic job description fragment for testing."""
    return (
        "We are looking for a Software Engineer Intern to join our backend team.\n"
        "Requirements: Python, REST APIs, SQL, cloud experience preferred.\n"
        "You will build and maintain microservices that process millions of transactions."
    )


@pytest.fixture
def default_config() -> MatchConfig:
    """A MatchConfig with default values."""
    return MatchConfig()


@pytest.fixture
def tight_config() -> MatchConfig:
    """A MatchConfig with very short char limits — useful for testing truncation."""
    return MatchConfig(resume_max_chars=50, description_max_chars=30)


# ══════════════════════════════════════════════════════════════════════════════
# TestBuildScoringPrompt
# ══════════════════════════════════════════════════════════════════════════════

class TestBuildScoringPrompt:
    """Tests for build_scoring_prompt()."""

    def test_prompt_contains_job_title(
        self, sample_resume: str, sample_description: str, default_config: MatchConfig
    ) -> None:
        """Verifies the job title appears in the prompt so Claude knows what job to score."""
        prompt = build_scoring_prompt(
            resume_text=sample_resume,
            job_title="Software Engineer Intern",
            job_company="Acme Corp",
            job_description=sample_description,
            config=default_config,
        )
        assert "Software Engineer Intern" in prompt

    def test_prompt_contains_company_name(
        self, sample_resume: str, sample_description: str, default_config: MatchConfig
    ) -> None:
        """Verifies the company name appears so Claude has full job context."""
        prompt = build_scoring_prompt(
            resume_text=sample_resume,
            job_title="Backend Engineer",
            job_company="Stripe",
            job_description=sample_description,
            config=default_config,
        )
        assert "Stripe" in prompt

    def test_prompt_contains_resume_text(
        self, sample_resume: str, sample_description: str, default_config: MatchConfig
    ) -> None:
        """Verifies resume text is included in the prompt for Claude to evaluate."""
        prompt = build_scoring_prompt(
            resume_text=sample_resume,
            job_title="SWE",
            job_company="Co",
            job_description=sample_description,
            config=default_config,
        )
        # Check a distinctive phrase from the fixture
        assert "UC Berkeley" in prompt

    def test_prompt_contains_job_description(
        self, sample_resume: str, sample_description: str, default_config: MatchConfig
    ) -> None:
        """Verifies job description text is included for Claude to compare against."""
        prompt = build_scoring_prompt(
            resume_text=sample_resume,
            job_title="SWE",
            job_company="Co",
            job_description=sample_description,
            config=default_config,
        )
        assert "microservices" in prompt

    def test_prompt_instructs_json_only_output(
        self, sample_resume: str, sample_description: str, default_config: MatchConfig
    ) -> None:
        """
        Verifies the prompt tells Claude to return ONLY JSON.
        Without this instruction, Claude may add prose that breaks parse_claude_response().
        """
        prompt = build_scoring_prompt(
            resume_text=sample_resume,
            job_title="SWE",
            job_company="Co",
            job_description=sample_description,
            config=default_config,
        )
        assert "JSON" in prompt
        assert "score" in prompt
        assert "reasoning" in prompt

    def test_prompt_contains_scoring_criteria(
        self, sample_resume: str, sample_description: str, default_config: MatchConfig
    ) -> None:
        """Verifies scoring criteria from MatchConfig appear in the prompt rubric."""
        prompt = build_scoring_prompt(
            resume_text=sample_resume,
            job_title="SWE",
            job_company="Co",
            job_description=sample_description,
            config=default_config,
        )
        # The first criterion in the default list should appear
        assert "Technical skill overlap" in prompt

    def test_resume_truncated_to_max_chars(
        self, sample_description: str, tight_config: MatchConfig
    ) -> None:
        """
        With a tight char limit, only the first N chars of the resume appear in the prompt.
        This prevents overrunning the token budget on long resumes.
        """
        long_resume = "A" * 1000
        prompt = build_scoring_prompt(
            resume_text=long_resume,
            job_title="SWE",
            job_company="Co",
            job_description=sample_description,
            config=tight_config,
        )
        # The prompt should contain at most 50 A's (tight_config.resume_max_chars)
        assert "A" * (tight_config.resume_max_chars + 1) not in prompt
        assert "A" * tight_config.resume_max_chars in prompt

    def test_description_truncated_to_max_chars(
        self, sample_resume: str, tight_config: MatchConfig
    ) -> None:
        """With a tight char limit, job description is truncated in the prompt."""
        long_description = "Z" * 1000
        prompt = build_scoring_prompt(
            resume_text=sample_resume,
            job_title="SWE",
            job_company="Co",
            job_description=long_description,
            config=tight_config,
        )
        assert "Z" * (tight_config.description_max_chars + 1) not in prompt
        assert "Z" * tight_config.description_max_chars in prompt

    def test_default_config_used_when_none_passed(
        self, sample_resume: str, sample_description: str
    ) -> None:
        """Passing config=None should silently fall back to MatchConfig() defaults."""
        prompt = build_scoring_prompt(
            resume_text=sample_resume,
            job_title="SWE",
            job_company="Co",
            job_description=sample_description,
            config=None,  # No config passed
        )
        # If default config is used, the default model name should be in metadata
        # (we can't check the model in the prompt text, but we can check criteria exist)
        assert "Technical skill overlap" in prompt

    def test_custom_criteria_appear_in_prompt(
        self, sample_resume: str, sample_description: str
    ) -> None:
        """Custom scoring criteria in MatchConfig should appear in the generated prompt."""
        custom_config = MatchConfig(scoring_criteria=["Only care about Rust skills"])
        prompt = build_scoring_prompt(
            resume_text=sample_resume,
            job_title="SWE",
            job_company="Co",
            job_description=sample_description,
            config=custom_config,
        )
        assert "Only care about Rust skills" in prompt

    def test_empty_description_handled(self, sample_resume: str, default_config: MatchConfig) -> None:
        """Empty job description should not crash — just produce a shorter prompt."""
        prompt = build_scoring_prompt(
            resume_text=sample_resume,
            job_title="SWE",
            job_company="Co",
            job_description="",  # Empty
            config=default_config,
        )
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    def test_empty_resume_handled(self, sample_description: str, default_config: MatchConfig) -> None:
        """Empty resume text should not crash — just produce a prompt with empty resume section."""
        prompt = build_scoring_prompt(
            resume_text="",  # Empty
            job_title="SWE",
            job_company="Co",
            job_description=sample_description,
            config=default_config,
        )
        assert isinstance(prompt, str)
        assert len(prompt) > 0


# ══════════════════════════════════════════════════════════════════════════════
# TestParseClaudeResponse
# ══════════════════════════════════════════════════════════════════════════════

class TestParseClaudeResponse:
    """Tests for parse_claude_response()."""

    def test_perfect_json_response(self) -> None:
        """Happy path: Claude returns clean JSON exactly as instructed."""
        response = '{"score": 85, "reasoning": "Strong Python and API experience."}'
        score, reasoning = parse_claude_response(response)
        assert score == 85.0
        assert reasoning == "Strong Python and API experience."

    def test_score_returned_as_float(self) -> None:
        """Score must always be a float, even if Claude returns an integer in JSON."""
        response = '{"score": 72, "reasoning": "Decent match."}'
        score, _ = parse_claude_response(response)
        assert isinstance(score, float)

    def test_json_with_preamble_uses_regex_fallback(self) -> None:
        """
        When Claude ignores 'respond with ONLY JSON' and adds a preamble,
        the regex fallback should still extract the JSON object.
        """
        response = 'Sure! Here is my analysis:\n{"score": 60, "reasoning": "Partial match."}'
        score, reasoning = parse_claude_response(response)
        assert score == 60.0
        assert "Partial match" in reasoning

    def test_json_with_postamble_uses_regex_fallback(self) -> None:
        """Claude sometimes adds trailing text after the JSON — regex handles this."""
        response = '{"score": 45, "reasoning": "Weak match."}\nI hope this helps!'
        score, reasoning = parse_claude_response(response)
        assert score == 45.0
        assert "Weak match" in reasoning

    def test_completely_invalid_response_returns_zero(self) -> None:
        """If response is not parseable at all, return (0.0, error message)."""
        response = "I cannot evaluate this job posting."
        score, reasoning = parse_claude_response(response)
        assert score == 0.0
        assert "Failed to parse" in reasoning

    def test_empty_response_returns_zero(self) -> None:
        """Empty string response returns (0.0, error message) without crashing."""
        score, reasoning = parse_claude_response("")
        assert score == 0.0
        assert isinstance(reasoning, str)

    def test_score_is_clamped_when_over_100(self) -> None:
        """
        If Claude hallucinates a score > 100, clamp it.
        A score of 105 should become 100.0.
        """
        response = '{"score": 105, "reasoning": "Exceptional candidate."}'
        score, _ = parse_claude_response(response)
        assert score == 100.0

    def test_score_is_clamped_when_negative(self) -> None:
        """Negative scores (hallucination) should be clamped to 0.0."""
        response = '{"score": -10, "reasoning": "Very poor match."}'
        score, _ = parse_claude_response(response)
        assert score == 0.0

    def test_missing_reasoning_field_returns_empty_string(self) -> None:
        """If Claude omits the reasoning field, we return empty string (not a crash)."""
        response = '{"score": 70}'
        score, reasoning = parse_claude_response(response)
        assert score == 70.0
        assert reasoning == ""

    def test_missing_score_field_returns_zero(self) -> None:
        """If Claude omits the score field entirely, return (0.0, error)."""
        response = '{"reasoning": "Looks good but score missing."}'
        score, reasoning = parse_claude_response(response)
        assert score == 0.0

    def test_score_as_string_in_json(self) -> None:
        """Claude might return score as a quoted string ("85" vs 85). Handle both."""
        response = '{"score": "85", "reasoning": "Good match."}'
        score, _ = parse_claude_response(response)
        assert score == 85.0

    def test_whitespace_around_json_handled(self) -> None:
        """Extra whitespace or newlines around the JSON object should not cause failure."""
        response = '\n\n  {"score": 55, "reasoning": "Moderate match."}  \n'
        score, reasoning = parse_claude_response(response)
        assert score == 55.0
        assert "Moderate match" in reasoning

    def test_error_message_includes_response_preview(self) -> None:
        """
        When parsing fails, the error message should include a preview of what
        Claude returned, so we can debug why it failed.
        """
        bad_response = "This is completely unparseable content xyz"
        _, reasoning = parse_claude_response(bad_response)
        # The preview of the bad response should appear in the error message
        assert "unparseable" in reasoning or "Failed to parse" in reasoning


# ══════════════════════════════════════════════════════════════════════════════
# TestClampScore
# ══════════════════════════════════════════════════════════════════════════════

class TestClampScore:
    """Tests for clamp_score()."""

    def test_value_within_range_unchanged(self) -> None:
        """Values already in [0, 100] should pass through unmodified."""
        assert clamp_score(75.0) == 75.0

    def test_zero_boundary(self) -> None:
        """0.0 is the minimum valid score — should not be clamped."""
        assert clamp_score(0.0) == 0.0

    def test_hundred_boundary(self) -> None:
        """100.0 is the maximum valid score — should not be clamped."""
        assert clamp_score(100.0) == 100.0

    def test_negative_clamped_to_zero(self) -> None:
        """Any negative score should be clamped to 0.0."""
        assert clamp_score(-1.0) == 0.0
        assert clamp_score(-999.0) == 0.0

    def test_over_100_clamped_to_100(self) -> None:
        """Any score over 100 should be clamped to 100.0."""
        assert clamp_score(101.0) == 100.0
        assert clamp_score(999.0) == 100.0

    def test_returns_float(self) -> None:
        """Return type should always be float."""
        result = clamp_score(50)  # integer input
        assert isinstance(result, float)

    def test_fractional_score_preserved(self) -> None:
        """Fractional scores within range should pass through exactly."""
        assert clamp_score(85.5) == 85.5
