"""
tests/unit/test_orchestrator_logic.py — Unit tests for orchestrator_logic.py pure functions.

WHAT WE'RE TESTING:
  - build_tool_definitions: returns exactly 5 tools with required fields
  - parse_tool_calls: correctly extracts tool_use blocks from Claude response content
  - parse_tool_calls: returns empty list when no tool_use blocks present
  - build_system_prompt: includes the user's goal and DB state summary
  - OrchestratorConfig: defaults are correct
  - build_tool_result_message: produces the correct Anthropic tool_result format

All functions are pure — no DB, no API, no mocks needed.
"""

import json

import pytest

from agents.orchestrator_logic import (
    OrchestratorConfig,
    OrchestratorResult,
    build_system_prompt,
    build_tool_definitions,
    build_tool_result_message,
    parse_tool_calls,
)


class TestBuildToolDefinitions:
    def test_returns_six_tools(self) -> None:
        """The Orchestrator exposes exactly 6 tools to Claude."""
        tools = build_tool_definitions()
        assert len(tools) == 6

    def test_all_tools_have_required_fields(self) -> None:
        """Each tool definition must have name, description, and input_schema."""
        tools = build_tool_definitions()
        for tool in tools:
            assert "name" in tool, f"Tool missing 'name': {tool}"
            assert "description" in tool, f"Tool missing 'description': {tool}"
            assert "input_schema" in tool, f"Tool missing 'input_schema': {tool}"

    def test_tool_names_are_correct(self) -> None:
        """Tool names must exactly match what orchestrator.py dispatches on."""
        tools = build_tool_definitions()
        names = {t["name"] for t in tools}
        assert names == {
            "check_db_state",
            "scrape_jobs",
            "score_jobs",
            "auto_review_jobs",
            "get_reviewed_jobs",
            "request_apply_approval",
        }

    def test_request_apply_approval_requires_job_ids(self) -> None:
        """The approval tool must require job_ids as a parameter."""
        tools = build_tool_definitions()
        approval_tool = next(t for t in tools if t["name"] == "request_apply_approval")
        schema = approval_tool["input_schema"]
        assert "job_ids" in schema["properties"]
        assert "job_ids" in schema["required"]

    def test_check_db_state_takes_no_args(self) -> None:
        """check_db_state has no required arguments — always callable."""
        tools = build_tool_definitions()
        check_tool = next(t for t in tools if t["name"] == "check_db_state")
        assert check_tool["input_schema"]["required"] == []


class TestParseToolCalls:
    def test_extracts_single_tool_call(self) -> None:
        """Extracts (id, name, input) from a single tool_use content block."""
        # Simulate what Anthropic's SDK returns in response.content
        class FakeToolUseBlock:
            type = "tool_use"
            id = "toolu_01AbCdEf"
            name = "check_db_state"
            input = {}

        content = [FakeToolUseBlock()]
        result = parse_tool_calls(content)
        assert len(result) == 1
        tool_id, tool_name, tool_input = result[0]
        assert tool_id == "toolu_01AbCdEf"
        assert tool_name == "check_db_state"
        assert tool_input == {}

    def test_extracts_multiple_tool_calls(self) -> None:
        """Claude can return multiple tool_use blocks in one response."""
        class FakeBlock:
            def __init__(self, block_id, name, inp):
                self.type = "tool_use"
                self.id = block_id
                self.name = name
                self.input = inp

        content = [
            FakeBlock("id-1", "check_db_state", {}),
            FakeBlock("id-2", "scrape_jobs", {}),
        ]
        result = parse_tool_calls(content)
        assert len(result) == 2
        assert result[0][1] == "check_db_state"
        assert result[1][1] == "scrape_jobs"

    def test_returns_empty_list_when_no_tool_calls(self) -> None:
        """When Claude returns only text (no tool calls), parse returns []."""
        class FakeTextBlock:
            type = "text"
            text = "I'm done with the session."

        content = [FakeTextBlock()]
        result = parse_tool_calls(content)
        assert result == []

    def test_ignores_text_blocks_alongside_tool_calls(self) -> None:
        """Text blocks mixed with tool_use blocks — only tool_use is extracted."""
        class FakeTextBlock:
            type = "text"
            text = "Let me check the database first."

        class FakeToolUseBlock:
            type = "tool_use"
            id = "toolu_xyz"
            name = "check_db_state"
            input = {}

        content = [FakeTextBlock(), FakeToolUseBlock()]
        result = parse_tool_calls(content)
        assert len(result) == 1
        assert result[0][1] == "check_db_state"

    def test_returns_empty_for_empty_content(self) -> None:
        """Empty content list produces an empty result."""
        assert parse_tool_calls([]) == []


class TestBuildSystemPrompt:
    def test_includes_goal(self) -> None:
        """The system prompt must contain the user's exact goal string."""
        goal = "Find me 5 good SWE jobs to apply to"
        prompt = build_system_prompt(goal, {})
        assert goal in prompt

    def test_includes_db_counts(self) -> None:
        """DB state counts appear in the system prompt for Claude's context."""
        db_state = {"total": 20, "new": 5, "scored": 10, "reviewed": 3, "ignored": 2, "applied": 0}
        prompt = build_system_prompt("test goal", db_state)
        assert "20" in prompt  # total
        assert "5" in prompt   # new
        assert "10" in prompt  # scored

    def test_includes_approval_constraint(self) -> None:
        """The system prompt must mention the human approval requirement."""
        prompt = build_system_prompt("find jobs", {})
        assert "approval" in prompt.lower() or "approve" in prompt.lower()

    def test_handles_empty_db_state(self) -> None:
        """build_system_prompt doesn't crash with an empty db_state dict."""
        prompt = build_system_prompt("test", {})
        # Should show zeros for all missing counts
        assert "0" in prompt


class TestBuildToolResultMessage:
    def test_returns_correct_format(self) -> None:
        """Tool result must match the Anthropic API's expected tool_result format."""
        msg = build_tool_result_message("toolu_abc123", {"total": 5})
        assert msg["role"] == "user"
        assert len(msg["content"]) == 1
        block = msg["content"][0]
        assert block["type"] == "tool_result"
        assert block["tool_use_id"] == "toolu_abc123"

    def test_result_is_json_serialized(self) -> None:
        """The result dict must be serialized to a JSON string (Anthropic requires this)."""
        result_data = {"total": 42, "new": 10}
        msg = build_tool_result_message("toolu_xyz", result_data)
        content_str = msg["content"][0]["content"]
        assert isinstance(content_str, str)
        # Should be valid JSON
        parsed = json.loads(content_str)
        assert parsed["total"] == 42


class TestOrchestratorConfig:
    def test_default_values(self) -> None:
        """OrchestratorConfig defaults match the intended settings."""
        config = OrchestratorConfig()
        assert config.model == "claude-haiku-4-5-20251001"
        assert config.max_turns == 10
        assert config.max_tokens == 4096
        assert config.dry_run is False

    def test_custom_values(self) -> None:
        """OrchestratorConfig accepts custom values at instantiation."""
        config = OrchestratorConfig(model="claude-sonnet-4-6", max_turns=5, dry_run=True)
        assert config.model == "claude-sonnet-4-6"
        assert config.max_turns == 5
        assert config.dry_run is True


class TestOrchestratorResult:
    def test_default_values(self) -> None:
        """OrchestratorResult has correct defaults for a fresh session."""
        import uuid
        session_id = uuid.uuid4()
        result = OrchestratorResult(session_id=session_id)
        assert result.status == "running"
        assert result.steps == []
        assert result.token_usage == 0
        assert result.result_summary == ""
        assert result.errors == []
