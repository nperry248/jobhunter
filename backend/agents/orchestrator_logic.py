"""
agents/orchestrator_logic.py — Pure functions for the Orchestrator agent.

ARCHITECTURE (Functional Core / Imperative Shell):
  This file is the "Functional Core" — zero I/O, zero side effects.
  All functions take plain data in, return plain data out.

  Why this matters:
    Unit tests for pure functions need NO mocking — you just call the function
    with test inputs and assert the output. No DB, no API calls, no Playwright.

  The "Imperative Shell" (orchestrator.py) handles:
    - Claude API calls
    - DB reads/writes
    - Spawning real agents (scraper, resume_match, apply)

WHAT'S IN HERE:
  - OrchestratorConfig:      configuration dataclass (model, max_turns, etc.)
  - OrchestratorResult:      result summary dataclass (status, steps, token_usage)
  - build_tool_definitions:  the 5 tools Claude can call, in Anthropic API format
  - build_system_prompt:     constructs the system prompt with goal + DB snapshot
  - parse_tool_calls:        extracts tool name + input from Claude's response
  - build_tool_result_message: builds the tool_result block to send back to Claude

CONCEPT — Anthropic tool use:
  Instead of having Claude return free-form text, we give it a list of "tools"
  (functions) it can call. Claude returns a structured JSON object saying which
  tool to call and with what arguments. We execute the tool, return the result,
  and Claude decides what to do next. This loop continues until Claude calls
  `end_turn` or we hit max_turns.

  This is sometimes called "function calling" (OpenAI's terminology) or
  "tool use" (Anthropic's terminology). They're the same concept.
"""

import uuid
from dataclasses import dataclass, field


# ── Configuration ─────────────────────────────────────────────────────────────

@dataclass
class OrchestratorConfig:
    """
    Configuration for one Orchestrator session.

    WHY A DATACLASS OVER PASSING SETTINGS DIRECTLY:
      Decouples the agent loop from the global settings object. Tests can inject
      custom configs (e.g. dry_run=True, max_turns=2) without monkey-patching
      module-level settings. Same pattern as ApplyConfig and MatchConfig.

    Fields:
        model:      Which Claude model to use for decision-making.
        max_turns:  Hard cap on tool calls. Safety valve against infinite loops.
        max_tokens: Max tokens per Claude response.
        dry_run:    If True, tools log mock results instead of running real agents.
        mode:       "fresh_scan" — full pipeline (scrape → score → auto-review → apply).
                    "use_reviewed" — only work with jobs already reviewed in the UI.
    """
    model: str = "claude-haiku-4-5-20251001"
    max_turns: int = 10
    max_tokens: int = 4096
    dry_run: bool = False
    mode: str = "fresh_scan"
    max_apply: int = 5   # cap on how many jobs get auto-reviewed and sent to approval


# ── Result Summary ─────────────────────────────────────────────────────────────

@dataclass
class OrchestratorResult:
    """
    Summary of one Orchestrator session. Returned by run() and resume().

    Fields:
        session_id:     UUID of the OrchestratorSession DB record.
        status:         Final state: "complete" | "waiting_for_approval" | "failed"
        steps:          Ordered list of tool call + result dicts (the reasoning log).
        token_usage:    Total input + output tokens used across all Claude API calls.
        result_summary: Claude's plain-English summary of the session outcome.
        errors:         List of human-readable error messages (one per failed step).

    WHY INCLUDE errors SEPARATELY FROM steps:
      steps contains the full structured log (for the frontend reasoning panel).
      errors is a flat list so callers can quickly check "did anything go wrong?"
      without walking the entire steps list.
    """
    session_id: uuid.UUID
    status: str = "running"
    steps: list[dict] = field(default_factory=list)
    token_usage: int = 0
    result_summary: str = ""
    errors: list[str] = field(default_factory=list)


# ── Tool Definitions ───────────────────────────────────────────────────────────

def build_tool_definitions() -> list[dict]:
    """
    Return the 5 tool definitions in Anthropic API format.

    CONCEPT — Tool definitions:
      Each tool has:
        - name:        What Claude calls to invoke it (snake_case)
        - description: Plain English explaining WHEN and WHY to use this tool.
                       Claude reads this to decide which tool to call — the better
                       the description, the better Claude's decisions.
        - input_schema: JSON Schema describing the tool's parameters. Claude uses
                        this to fill in the correct argument types/values.

    THE 6 TOOLS:
      1. check_db_state         — Read-only snapshot of current DB counts.
      2. scrape_jobs            — Trigger the Scraper Agent to fetch new listings.
      3. score_jobs             — Trigger the Resume Match Agent to score unscored jobs.
      4. auto_review_jobs       — Auto-mark scored jobs above a threshold as reviewed.
      5. get_reviewed_jobs      — Fetch the list of REVIEWED jobs ready for applying.
      6. request_apply_approval — Pause and ask the human to approve before applying.

    WHY NO `apply_jobs` TOOL:
      We never let Claude directly apply to jobs. Applying is irreversible — you
      can't un-submit an application. Instead, Claude calls `request_apply_approval`,
      which pauses the loop and surfaces the job list to the human. The human
      explicitly approves via POST /approve/{id}. Only then does the Apply Agent run.
      This is the "human-in-the-loop" safety gate.

    Returns:
        List of tool definition dicts ready to pass to the Anthropic client.
    """
    return [
        {
            "name": "check_db_state",
            "description": (
                "Check the current state of the job database. Returns counts of jobs "
                "in each status (new, scored, reviewed, ignored, applied) and how many "
                "jobs are unscored. Use this at the start of a session and after any "
                "agent run to decide what to do next."
            ),
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
        {
            "name": "scrape_jobs",
            "description": (
                "Run the Scraper Agent to fetch new job listings from Greenhouse and "
                "Lever. Use this when there are no new unscored jobs in the DB or when "
                "the user wants fresh listings. Returns counts of new jobs added, "
                "duplicates skipped, and errors encountered."
            ),
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
        {
            "name": "score_jobs",
            "description": (
                "Run the Resume Match Agent to score all unscored jobs in the DB against "
                "the user's resume using Claude AI. Use this after scraping to rank which "
                "jobs are worth applying to. Returns counts of jobs scored and any errors."
            ),
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
        {
            "name": "auto_review_jobs",
            "description": (
                "Automatically mark SCORED jobs above a score threshold as REVIEWED, "
                "making them eligible for the apply step. Use this in fresh-scan mode "
                "after scoring, so the pipeline can proceed without requiring manual review "
                "in the UI. Only use this when the session mode is 'fresh_scan'. "
                "Always pass the limit parameter to control how many jobs are promoted."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "min_score": {
                        "type": "number",
                        "description": (
                            "Only mark jobs with match_score >= this value as reviewed. "
                            "Defaults to 70.0. Higher threshold = fewer, better-matched jobs."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": (
                            "Maximum number of jobs to mark as reviewed. "
                            "Use the max_apply value from the session config. "
                            "Jobs are selected best-score-first up to this limit."
                        ),
                    },
                },
                "required": [],
            },
        },
        {
            "name": "get_reviewed_jobs",
            "description": (
                "Fetch the list of jobs that have been marked as REVIEWED — either "
                "manually by the user in the UI, or automatically via auto_review_jobs. "
                "Returns job details including title, company, score, and URL. "
                "Use this to see which jobs are ready to apply to before requesting approval."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "min_score": {
                        "type": "number",
                        "description": (
                            "Only include jobs with match_score >= this value. "
                            "Defaults to 70.0 if not specified."
                        ),
                    }
                },
                "required": [],
            },
        },
        {
            "name": "request_apply_approval",
            "description": (
                "Pause the session and ask the human to review and approve the job list "
                "before submitting any applications. ALWAYS call this before applying — "
                "never apply without explicit human approval. Pass the job UUIDs you want "
                "to apply to. The session will pause until the user approves or rejects."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "job_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of job UUIDs (strings) to request approval for.",
                    },
                    "reasoning": {
                        "type": "string",
                        "description": (
                            "Brief explanation of why these jobs were selected "
                            "(e.g. 'All scored above 80% and match the user's Python background')."
                        ),
                    },
                },
                "required": ["job_ids", "reasoning"],
            },
        },
    ]


# ── System Prompt ──────────────────────────────────────────────────────────────

def build_system_prompt(goal: str, db_state: dict, mode: str = "fresh_scan", max_apply: int = 5) -> str:
    """
    Build the system prompt for the Orchestrator's Claude API call.

    CONCEPT — System prompt:
      The system prompt is the "instructions" block Claude reads before any
      conversation. It sets the agent's persona, constraints, and context.
      Unlike user messages, the system prompt is not part of the turn-by-turn
      conversation — it's permanent background knowledge.

    WHAT WE INCLUDE:
      1. Role definition: what the Orchestrator is and what it can do
      2. The user's goal: what they want accomplished this session
      3. Current DB state: snapshot of job counts so Claude starts informed
      4. Mode-specific workflow: what steps to follow for fresh_scan vs use_reviewed
      5. Hard constraints: the approval gate, max turns, never apply directly

    Args:
        goal:     The user's natural-language goal for this session.
        db_state: Dict with current job counts (new, scored, reviewed, etc.)
                  Typically from `check_db_state`'s return value.
        mode:     "fresh_scan" — run the full pipeline (scrape → score → auto-review → apply).
                  "use_reviewed" — only work with jobs already reviewed manually in the UI.

    Returns:
        The system prompt string to pass to the Anthropic client as the `system` param.
    """
    db_summary = (
        f"  - Total jobs in DB: {db_state.get('total', 0)}\n"
        f"  - New (unscored): {db_state.get('new', 0)}\n"
        f"  - Scored (not yet reviewed): {db_state.get('scored', 0)}\n"
        f"  - Reviewed (approved for applying): {db_state.get('reviewed', 0)}\n"
        f"  - Ignored: {db_state.get('ignored', 0)}\n"
        f"  - Applied: {db_state.get('applied', 0)}"
    )

    if mode == "use_reviewed":
        workflow = f"""WORKFLOW (Use Reviewed mode — work with manually-reviewed jobs only):
1. check_db_state — understand the current DB snapshot.
2. get_reviewed_jobs — fetch jobs the user has already reviewed and approved in the UI.
   Pass min_score=70 and limit results to the best {max_apply} jobs.
3. If reviewed jobs exist, call request_apply_approval with those job IDs.
4. If no reviewed jobs exist, summarize that and stop — do NOT scrape or auto-review.

Do NOT call scrape_jobs, score_jobs, or auto_review_jobs in this mode.
Call each tool at most ONCE. Do not loop."""
    else:
        # fresh_scan (default)
        #
        # IMPORTANT: The paths below are evaluated in ORDER — take the FIRST one that
        # matches the check_db_state result. They are mutually exclusive by design.
        # PATH B is listed first because an empty DB must be handled before any other check.
        workflow = f"""WORKFLOW (Fresh Scan mode — evaluate check_db_state then follow exactly ONE path):

1. Call check_db_state to get current counts (total, new, scored, reviewed, applied).

2. Take the FIRST path below whose condition matches. Paths are in priority order:

   PATH B — total == 0  (DB has no jobs at all):
     a. scrape_jobs
     b. score_jobs
     c. auto_review_jobs  (min_score=70, limit={max_apply})
     d. get_reviewed_jobs
     e. request_apply_approval

   PATH A — new > 0  (unscored jobs exist, even if other jobs are already scored):
     a. score_jobs
     b. auto_review_jobs  (min_score=70, limit={max_apply})
     c. get_reviewed_jobs
     d. request_apply_approval

   PATH C — new == 0 AND scored > 0  (all jobs already scored, none unscored):
     a. auto_review_jobs  (min_score=70, limit={max_apply})
     b. get_reviewed_jobs
     c. request_apply_approval

   PATH D — new == 0 AND scored == 0 AND reviewed > 0  (jobs already manually reviewed):
     a. get_reviewed_jobs
     b. request_apply_approval

   NO ACTIONABLE JOBS — none of the above match:
     Summarize what you found and stop. Do NOT call scrape or score without clear reason.

CRITICAL RULES:
  - Evaluate paths in the order listed above. Take the FIRST matching path.
  - Call each tool at most ONCE. Do not loop or repeat steps.
  - After request_apply_approval, stop immediately — do not call any more tools."""

    return f"""You are JobHunter AI's Orchestrator agent. Your job is to coordinate a job-hunting pipeline and prepare a small batch of applications for human approval.

GOAL FOR THIS SESSION:
{goal}

CURRENT DATABASE STATE:
{db_summary}

SESSION MODE: {"Fresh Scan (full pipeline)" if mode == "fresh_scan" else "Use Reviewed (manual review only)"}
MAX JOBS TO APPLY: {max_apply}

{workflow}

AVAILABLE TOOLS:
- check_db_state: See current job counts.
- scrape_jobs: Fetch new job listings from Greenhouse and Lever.
- score_jobs: Score unscored jobs against the user's resume using AI.
- auto_review_jobs(min_score, limit): Promote top scored jobs to reviewed. Always pass limit={max_apply}.
- get_reviewed_jobs(min_score): Get the list of reviewed jobs ready to apply to.
- request_apply_approval(job_ids, reasoning): Pause for human approval. REQUIRED before applying.

FAILURE GUIDANCE:
- If score_jobs returns an error (e.g. resume not found), stop and say what went wrong.
- If auto_review_jobs returns reviewed=0, the jobs scored below the threshold. Stop and say so.
- If get_reviewed_jobs returns an empty list, say "no jobs above the score threshold" — NOT "database is empty".
- Never say "database is empty" unless check_db_state actually returned total=0.

When you finish, write a one-sentence summary of what was accomplished."""


# ── Response Parsing ───────────────────────────────────────────────────────────

def parse_tool_calls(response_content: list) -> list[tuple[str, str, dict]]:
    """
    Extract tool call information from Claude's response content blocks.

    CONCEPT — Claude response structure:
      When Claude decides to call a tool, its response looks like this:
        {
          "content": [
            {"type": "text", "text": "I'll check the database first..."},
            {
              "type": "tool_use",
              "id": "toolu_01AbCdEf...",
              "name": "check_db_state",
              "input": {}
            }
          ]
        }

      Claude can return MULTIPLE tool calls in one response (parallel calling).
      We extract all of them and execute them in order.

    Args:
        response_content: The `response.content` list from a Claude API response.

    Returns:
        List of (tool_use_id, tool_name, tool_input) tuples.
        Returns an empty list if Claude's response contains no tool calls
        (meaning it's done — final text response only).
    """
    tool_calls = []
    for block in response_content:
        # Each content block has a `type` — we only care about "tool_use" blocks.
        # "text" blocks are Claude's reasoning/explanation — we log them but don't execute.
        if hasattr(block, "type") and block.type == "tool_use":
            tool_calls.append((block.id, block.name, block.input))
    return tool_calls


def build_tool_result_message(tool_use_id: str, result: dict) -> dict:
    """
    Build a tool_result content block to send back to Claude.

    CONCEPT — The tool-use conversation loop:
      After we execute a tool, we must tell Claude what happened so it can
      decide its next action. The Anthropic API expects a very specific format:

        {
          "role": "user",
          "content": [
            {
              "type": "tool_result",
              "tool_use_id": "<id from Claude's tool_use block>",
              "content": "<JSON string of the result>"
            }
          ]
        }

      The `tool_use_id` MUST match the `id` from Claude's tool_use block.
      This is how Claude knows which tool call produced which result.

    Args:
        tool_use_id: The `id` field from Claude's tool_use content block.
        result:      The dict returned by _execute_tool(). Will be JSON-serialized.

    Returns:
        A dict in the format the Anthropic API expects for tool results.
    """
    import json

    return {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                # NOTE: Anthropic expects tool result content as a string, not a dict.
                # We serialize the result dict to a compact JSON string here.
                "content": json.dumps(result),
            }
        ],
    }
