"""
agents/resume_match_logic.py — Pure scoring functions for the Resume Match Agent.

ARCHITECTURE — WHY THIS FILE EXISTS (Functional Core / Imperative Shell):
  The Resume Match Agent has two distinct concerns:
    1. "What is the scoring logic?" — pure computation, no side effects
    2. "How do we orchestrate it?" — DB queries, API calls, async scheduling

  This file handles concern #1. It contains ONLY pure functions:
    - Takes plain Python values (strings, dicts) as input
    - Returns plain Python values as output
    - Makes NO DB calls, NO HTTP calls, NO file I/O
    - Deterministic: same input → same output, every time

  WHY THIS MATTERS FOR TESTING:
    Pure functions need zero mocking. You call them with test data and assert
    the output. That's it. Tests for this file run in milliseconds with no
    external dependencies at all.

  The "Imperative Shell" (resume_match.py) handles concern #2 and uses mocks
  in its tests to fake the DB + Claude API.

CONCEPT — Dataclasses:
  @dataclass automatically generates __init__, __repr__, and __eq__ from the
  annotated fields. It's like a lightweight struct — use it when you want a
  typed container for related config values without the overhead of Pydantic.
"""

import json
import re
from dataclasses import dataclass, field


# ── Configuration ─────────────────────────────────────────────────────────────

@dataclass
class MatchConfig:
    """
    Configuration that controls how the scoring prompt is built.

    WHY A DATACLASS INSTEAD OF HARDCODING:
      Config values in code are impossible to test in isolation — you'd have to
      change source files to test different prompts. A dataclass lets tests pass
      custom configs without monkey-patching anything.

    Fields:
        model: The Claude model ID to use for scoring.
        max_tokens: Upper limit on Claude's response length. 200 tokens is plenty
                    for a JSON object with a score + one sentence of reasoning.
        resume_max_chars: We truncate the resume text to this length before sending
                          to Claude. A full resume is ~3000 chars; we cap it to
                          control token costs. 4000 chars ≈ ~1000 tokens.
        description_max_chars: Same idea for job descriptions, which can be very long.
                                2000 chars captures the key requirements.
        scoring_criteria: Human-readable rubric that Claude uses to score.
                          These are shown to Claude in the prompt, so wording matters.
    """

    # claude-haiku-4-5 is ~25x cheaper than Sonnet. Scoring 100 jobs costs ~$0.002.
    # Haiku is fast and capable enough for structured JSON output with a clear rubric.
    model: str = "claude-haiku-4-5-20251001"
    max_tokens: int = 200

    # Token budget: 4000 chars ≈ ~1000 tokens at ~4 chars/token (rough estimate).
    # Haiku's context window is 200k tokens, so this is very conservative.
    # Adjust if you find Claude missing important resume details.
    resume_max_chars: int = 4000
    description_max_chars: int = 2000

    # The rubric Claude uses to justify its score. Ordered by importance.
    scoring_criteria: list[str] = field(default_factory=lambda: [
        "Technical skill overlap between resume and job requirements",
        "Seniority level match (internship vs new grad vs mid-level)",
        "Industry/domain relevance (fintech, consumer, B2B, etc.)",
        "Education requirements (degree level, major, GPA if listed)",
        "Location/remote compatibility",
    ])


# ── Prompt Building ───────────────────────────────────────────────────────────

def build_scoring_prompt(
    resume_text: str,
    job_title: str,
    job_company: str,
    job_description: str,
    config: MatchConfig | None = None,
) -> str:
    """
    Build the Claude prompt that asks it to score a job against a resume.

    WHY WE SEPARATE PROMPT BUILDING FROM API CALLS:
      If build_scoring_prompt() and the API call were in one function, we'd have
      to call the real Claude API just to test that the prompt is well-formed.
      Separating them means we can unit-test the prompt shape for free.

    PROMPT DESIGN:
      We use a structured "role + task + rubric + format" pattern:
        - Role: "You are an expert recruiter..." — sets the scoring persona
        - Task: exactly what we want Claude to do
        - Rubric: the scoring_criteria list so Claude uses consistent weights
        - Format: strict JSON schema so parse_claude_response() can parse reliably

      The JSON-only instruction is important — without it, Claude may add prose
      before or after the JSON ("Sure! Here is your score: {...}"), which breaks parsing.

    Args:
        resume_text:      Plain text extracted from the PDF (after strip_html if needed).
        job_title:        e.g. "Software Engineer Intern"
        job_company:      e.g. "Airbnb"
        job_description:  Plain text job description (HTML already stripped).
        config:           Scoring config. Defaults to MatchConfig() if None.

    Returns:
        A complete prompt string ready to send to the Claude messages API.
    """
    if config is None:
        config = MatchConfig()

    # Truncate inputs to stay within the token budget.
    # NOTE: We truncate resume from the END (most resumes front-load key info)
    # and description from the END (job descriptions often end with boilerplate).
    resume_snippet = resume_text[: config.resume_max_chars]
    description_snippet = job_description[: config.description_max_chars]

    # Format the scoring criteria as a numbered list for readability.
    criteria_lines = "\n".join(
        f"  {i + 1}. {criterion}"
        for i, criterion in enumerate(config.scoring_criteria)
    )

    return f"""You are an expert technical recruiter evaluating candidate–job fit.

Score how well this candidate's resume matches the job posting below.

SCORING RUBRIC (consider these factors in order of importance):
{criteria_lines}

OUTPUT FORMAT — respond with ONLY valid JSON, no other text:
{{
  "score": <integer 0-100>,
  "reasoning": "<one sentence explaining the primary driver of the score>"
}}

Score meanings:
  90-100: Exceptional match — apply immediately
  70-89:  Strong match — worth applying
  50-69:  Partial match — apply if job search is slow
  30-49:  Weak match — significant gaps
  0-29:   Poor match — missing critical requirements

---

JOB: {job_title} at {job_company}

JOB DESCRIPTION:
{description_snippet}

---

CANDIDATE RESUME:
{resume_snippet}

---

Respond with ONLY the JSON object. No preamble, no explanation outside the JSON."""


# ── Response Parsing ──────────────────────────────────────────────────────────

def parse_claude_response(response_text: str) -> tuple[float, str]:
    """
    Parse Claude's response text into (score, reasoning).

    FALLBACK STRATEGY (two passes):
      Pass 1 — Direct JSON parse:
        If Claude followed instructions perfectly, response_text IS valid JSON.
        json.loads() handles it immediately.

      Pass 2 — Regex extraction:
        Sometimes Claude adds a preamble ("Sure! Here's the analysis:") or
        trailing text. Regex finds the first {...} block and tries to parse that.

      Final fallback:
        Return (0.0, error_message). We never crash; we log and move on.
        The job gets score=0.0 and will be filtered out by match_score_threshold.

    WHY NOT raise an exception?
      This function is called in a loop over hundreds of jobs. One bad Claude
      response should NOT abort the entire scoring run. Log the error and continue.

    Args:
        response_text: The raw string from Claude's completion.

    Returns:
        (score, reasoning) where score is clamped to [0.0, 100.0].
        On parse failure, returns (0.0, "<error description>").
    """
    # ── Pass 1: Direct JSON parse ─────────────────────────────────────────────
    try:
        data = json.loads(response_text.strip())
        score = float(data["score"])
        reasoning = str(data.get("reasoning", ""))
        return clamp_score(score), reasoning
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        pass  # Fall through to Pass 2

    # ── Pass 2: Regex extraction ──────────────────────────────────────────────
    # Find the first JSON object in the string (handles preamble/postamble).
    # re.DOTALL makes '.' match newlines so multi-line JSON objects are captured.
    match = re.search(r"\{.*?\}", response_text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group())
            score = float(data["score"])
            reasoning = str(data.get("reasoning", ""))
            return clamp_score(score), reasoning
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            pass

    # ── Final fallback ────────────────────────────────────────────────────────
    # Truncate the bad response in the error message so logs aren't flooded.
    preview = response_text[:100].replace("\n", " ")
    return 0.0, f"Failed to parse Claude response: {preview!r}"


# ── Score Utilities ───────────────────────────────────────────────────────────

def clamp_score(score: float) -> float:
    """
    Clamp a score to the valid range [0.0, 100.0].

    WHY THIS EXISTS:
      Claude might occasionally return 101 or -5 (hallucination or off-by-one).
      Rather than rejecting those responses entirely, we clamp them to the valid
      range. A score of 101 is "really 100" — same semantic meaning, valid range.

    Args:
        score: Any float value.

    Returns:
        float in [0.0, 100.0].
    """
    return float(max(0.0, min(100.0, score)))
