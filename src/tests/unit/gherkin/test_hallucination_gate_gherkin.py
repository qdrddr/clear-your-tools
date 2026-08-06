"""Gherkin steps for verify-only hallucination gate."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from cyt.common.agents import AgentName
from cyt_client.rules_file import extract_verify_only_flag
from cyt_client.sessions import append_tool_entries, read_session_log_file
from cyt_client.tool_gate import validate_pre_tool_call
from tests.unit.gherkin.conftest import GherkinContext
from tests.unit.gherkin.test_tool_catalog_gate_gherkin import (
    _patch_session_log_path,
    _write_session,
)

FEATURES = Path(__file__).resolve().parent / "features" / "hallucination_gate.feature"
scenarios(str(FEATURES))

pytestmark = pytest.mark.gherkin


@given(parsers.parse("agent {agent}"))
def given_agent(agent: str, gherkin_context: GherkinContext) -> None:
    gherkin_context.agent = cast(AgentName, agent)


def _verify_only_session_state(*, tools_inject_enabled: bool = False) -> dict:
    return {
        "kind": "session_state",
        "key": "session_state:inject",
        "tools_inject_enabled": tools_inject_enabled,
        "hallucination_gate_enabled": True,
    }


@given("a session log with tools inject disabled and no hallucination gate")
def given_skills_only_no_gate(gherkin_context: GherkinContext, tmp_path: Path) -> None:
    log_path = tmp_path / "session.jsonl"
    _write_session(
        log_path,
        [
            {
                "kind": "session_state",
                "key": "session_state:inject",
                "tools_inject_enabled": False,
            },
        ],
    )
    gherkin_context.payload = {"log_path": log_path, "session_id": "session-1"}


@given("a session log with tools inject disabled")
def given_skills_only(gherkin_context: GherkinContext, tmp_path: Path) -> None:
    given_skills_only_no_gate(gherkin_context, tmp_path)


@given(
    parsers.parse(
        "a verify-only session log with Type-2 cyt_mcp catalog tool {tool_name} path string",
    ),
)
def given_verify_only_cyt_mcp_catalog(
    tool_name: str,
    gherkin_context: GherkinContext,
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "session.jsonl"
    schema = {"type": "object", "properties": {"path": {"type": "string"}}}
    _write_session(
        log_path,
        [
            _verify_only_session_state(),
            {
                "kind": "tool_catalog",
                "key": "tool_catalog:cyt_mcp",
                "catalog": "cyt_mcp",
                "hash": "hash-cyt-mcp",
                "tools": [{"name": tool_name, "input_schema": schema}],
            },
        ],
    )
    gherkin_context.payload = {"log_path": log_path, "session_id": "session-1"}


@given(
    parsers.parse(
        "a verify-only session log with tools inject disabled and Type-2 cyt_mcp catalog tool {tool_name} path string",
    ),
)
def given_verify_only_explicit_branch(
    tool_name: str,
    gherkin_context: GherkinContext,
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "session.jsonl"
    schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }
    _write_session(
        log_path,
        [
            _verify_only_session_state(tools_inject_enabled=False),
            {
                "kind": "tool_catalog",
                "key": "tool_catalog:cyt_mcp",
                "catalog": "cyt_mcp",
                "hash": "hash-cyt-mcp",
                "tools": [{"name": tool_name, "input_schema": schema}],
            },
        ],
    )
    gherkin_context.payload = {"log_path": log_path, "session_id": "session-1"}


@given(parsers.parse("a session jsonl with Type-1 cyt_mcp tool {tool_name} hash {content_hash}"))
def given_type1_tool_on_disk(
    tool_name: str,
    content_hash: str,
    gherkin_context: GherkinContext,
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "session.jsonl"
    entry = {
        "kind": "tool",
        "key": f"tool:cyt_mcp:{tool_name}",
        "hash": content_hash,
        "full": True,
        "catalog": "cyt_mcp",
        "name": tool_name,
        "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
    }
    _write_session(log_path, [entry])
    gherkin_context.payload = {"log_path": log_path, "entry": entry}


@when(parsers.parse("appending Type-1 cyt_mcp tool {tool_name} hash {content_hash}"))
def when_append_type1_tool(
    tool_name: str,
    content_hash: str,
    gherkin_context: GherkinContext,
) -> None:
    log_path = gherkin_context.payload["log_path"]
    entry = dict(gherkin_context.payload["entry"])
    entry["hash"] = content_hash
    entry["name"] = tool_name
    append_tool_entries(Path(log_path), [entry])


@given("verify-only hook connect response with verify-only true")
def given_verify_only_hook_response(gherkin_context: GherkinContext) -> None:
    body = json.dumps(
        {
            "verify-only": True,
            "hookSpecificOutput": {},
            "cytSessionLog": [],
            "cytAgent": "cursor",
        },
    ).encode()
    gherkin_context.payload["hook_body"] = body


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


@then("deny reason should not mention get-tool-definitions")
def then_deny_no_get_tool_definitions(gherkin_context: GherkinContext) -> None:
    reason = str(gherkin_context.payload.get("reason") or "").casefold()
    assert "get-tool-definitions" not in reason


@then(parsers.parse("session jsonl should have {count:d} tool lines"))
def then_tool_line_count(count: int, gherkin_context: GherkinContext) -> None:
    log_path = Path(gherkin_context.payload["log_path"])
    _agent, entries = read_session_log_file(log_path)
    tool_lines = [entry for entry in entries if entry.get("kind") == "tool"]
    assert len(tool_lines) == count


@then("hook response is verify-only mode")
def then_hook_verify_only(gherkin_context: GherkinContext) -> None:
    body = gherkin_context.payload["hook_body"]
    assert extract_verify_only_flag(body) is True
