"""Gherkin steps for tool schema completeness regressions."""

from __future__ import annotations

from pathlib import Path

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from cyt_client.tool_gate import validate_pre_tool_call
from tests.unit.gherkin.conftest import GherkinContext
from tests.unit.gherkin.test_tool_catalog_gate_gherkin import (
    _cyt_mcp_catalog,
    _patch_session_log_path,
    _write_session,
)

FEATURES = Path(__file__).resolve().parent / "features" / "tool_schema_completeness.feature"
scenarios(str(FEATURES))

pytestmark = pytest.mark.gherkin


@given("agent cursor")
def given_agent_cursor(gherkin_context: GherkinContext) -> None:
    gherkin_context.agent = "cursor"


@given(
    parsers.parse(
        "a Type-2 cyt_mcp catalog with tool {tool_name} query string repo string required",
    ),
)
def given_semble_full_schema(
    tool_name: str,
    gherkin_context: GherkinContext,
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "session.jsonl"
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "repo": {"type": "string"},
        },
        "required": ["query", "repo"],
    }
    _write_session(log_path, _cyt_mcp_catalog(tool_name, schema))
    gherkin_context.payload = {"log_path": log_path, "session_id": "session-1"}


@given(
    parsers.parse(
        "a Type-2 cyt_mcp catalog with tool {tool_name} empty schema",
    ),
)
def given_semble_empty_schema(
    tool_name: str,
    gherkin_context: GherkinContext,
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "session.jsonl"
    _write_session(log_path, _cyt_mcp_catalog(tool_name, {}))
    gherkin_context.payload = {"log_path": log_path, "session_id": "session-1"}


@given(
    parsers.parse(
        "a Type-2 cyt_mcp catalog with tool {tool_name} query string required",
    ),
)
def given_query_only_schema(
    tool_name: str,
    gherkin_context: GherkinContext,
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "session.jsonl"
    schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }
    _write_session(log_path, _cyt_mcp_catalog(tool_name, schema))
    gherkin_context.payload = {"log_path": log_path, "session_id": "session-1"}


@when(
    parsers.parse(
        "preToolUse validates cyt-mcp tool {tool_name} with args {args}",
    ),
)
def when_validate_cyt_mcp(tool_name: str, args: str, gherkin_context: GherkinContext) -> None:
    arg_parts = [part.strip() for part in args.split() if part.strip()]
    tool_input: dict[str, str] = {}
    for index in range(0, len(arg_parts) - 1, 2):
        tool_input[arg_parts[index]] = arg_parts[index + 1]
    payload = {
        "hook_event_name": "preToolUse",
        "session_id": gherkin_context.payload.get("session_id", "session-1"),
        "tool_name": tool_name,
        "tool_input": tool_input,
        "cyt_agent": gherkin_context.agent,
    }
    monkeypatch = pytest.MonkeyPatch()
    _patch_session_log_path(monkeypatch, gherkin_context)
    validation = validate_pre_tool_call(payload)
    gherkin_context.payload["allowed"] = validation.allowed
    gherkin_context.payload["reason"] = validation.reason
    monkeypatch.undo()


@then("validation should allow")
def then_allow(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload.get("allowed") is True


@then("validation should deny")
def then_deny(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload.get("allowed") is False
    assert gherkin_context.payload.get("reason")
