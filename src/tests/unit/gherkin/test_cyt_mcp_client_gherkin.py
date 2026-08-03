"""Gherkin steps for cyt-mcp tool gate and pairing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from cyt.common.agents import AgentName
from cyt_client.tool_gate import normalize_mcp_tool_name, validate_pre_tool_call
from tests.unit.gherkin.conftest import GherkinContext

FEATURES = Path(__file__).resolve().parent / "features" / "cyt_mcp_client.feature"
scenarios(str(FEATURES))

pytestmark = pytest.mark.gherkin


@given(parsers.parse("agent {agent}"))
def given_agent(agent: str, gherkin_context: GherkinContext) -> None:
    gherkin_context.agent = cast(AgentName, agent)


@when(parsers.parse("MCP tool name {raw_name} is normalized"))
def when_normalize_name(raw_name: str, gherkin_context: GherkinContext) -> None:
    gherkin_context.payload["normalized"] = normalize_mcp_tool_name(
        raw_name,
        agent=gherkin_context.agent,
    )


@then(parsers.parse("normalized name should be {catalog_name}"))
def then_normalized(catalog_name: str, gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload["normalized"] == catalog_name


def _write_session_log(path: Path, tools: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(tool) for tool in tools]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@given("a session log with cyt-mcp tool filesystem_read_file")
def given_read_file_session(gherkin_context: GherkinContext, tmp_path: Path) -> None:
    log_path = tmp_path / "session.jsonl"
    _write_session_log(
        log_path,
        [
            {
                "kind": "tool",
                "key": "tool:cyt_mcp:filesystem_read_file",
                "name": "filesystem_read_file",
                "input_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                },
            },
        ],
    )
    gherkin_context.payload = {
        "log_path": log_path,
        "session_id": "session-1",
    }


@given("a session log with cyt-mcp tool demo_enum_tool")
def given_enum_session(gherkin_context: GherkinContext, tmp_path: Path) -> None:
    log_path = tmp_path / "session.jsonl"
    _write_session_log(
        log_path,
        [
            {
                "kind": "tool",
                "key": "tool:cyt_mcp:demo_enum_tool",
                "name": "demo_enum_tool",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "mode": {"type": "string", "enum": ["read", "write"]},
                    },
                },
            },
        ],
    )
    gherkin_context.payload = {
        "log_path": log_path,
        "session_id": "session-1",
    }


@given("a preToolUse payload calling filesystem_read_file with path /tmp/x")
def given_allow_payload(gherkin_context: GherkinContext) -> None:
    gherkin_context.payload["hook_payload"] = {
        "hook_event_name": "preToolUse",
        "session_id": gherkin_context.payload["session_id"],
        "tool_name": "filesystem_read_file",
        "tool_input": {"path": "/tmp/x"},
    }


@given("a preToolUse payload calling filesystem_write_file")
def given_unknown_tool_payload(gherkin_context: GherkinContext) -> None:
    gherkin_context.payload["hook_payload"] = {
        "hook_event_name": "preToolUse",
        "session_id": gherkin_context.payload["session_id"],
        "tool_name": "filesystem_write_file",
        "tool_input": {},
    }


@given("a preToolUse payload with an unknown property on filesystem_read_file")
def given_bad_property_payload(gherkin_context: GherkinContext) -> None:
    gherkin_context.payload["hook_payload"] = {
        "hook_event_name": "preToolUse",
        "session_id": gherkin_context.payload["session_id"],
        "tool_name": "filesystem_read_file",
        "tool_input": {"bogus": "value"},
    }


@given("a preToolUse payload with an invalid enum value")
def given_bad_enum_payload(gherkin_context: GherkinContext) -> None:
    gherkin_context.payload["hook_payload"] = {
        "hook_event_name": "preToolUse",
        "session_id": gherkin_context.payload["session_id"],
        "tool_name": "demo_enum_tool",
        "tool_input": {"mode": "delete"},
    }


@when("pre-tool validation runs")
def when_validate(gherkin_context: GherkinContext, monkeypatch: pytest.MonkeyPatch) -> None:
    log_path = gherkin_context.payload.get("log_path")
    if log_path is not None:
        monkeypatch.setattr(
            "cyt_client.tool_gate.session_log_path",
            lambda _payload: log_path,
        )
    else:
        monkeypatch.setattr(
            "cyt_client.tool_gate.session_log_path",
            lambda _payload: None,
        )
    allowed, reason = validate_pre_tool_call(gherkin_context.payload["hook_payload"])
    gherkin_context.payload["allowed"] = allowed
    gherkin_context.payload["reason"] = reason


@then("pre-tool validation should allow the call")
def then_allow(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload["allowed"] is True


@then("pre-tool validation should deny the call")
def then_deny(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload["allowed"] is False
    assert gherkin_context.payload["reason"]


@given("cyt_mcp is enabled in user config")
def given_cyt_mcp_enabled(gherkin_context: GherkinContext, tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "pruning:\n  tools:\n    hook:\n      tools_from:\n        - cyt_mcp\n",
        encoding="utf-8",
    )
    gherkin_context.payload["_config_path"] = config_path
    gherkin_context.payload["_cyt_mcp_enabled"] = True


@given("cyt_mcp is not enabled in user config")
def given_cyt_mcp_disabled(gherkin_context: GherkinContext, tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "pruning:\n  tools:\n    hook:\n      tools_from:\n        - mcpc\n",
        encoding="utf-8",
    )
    gherkin_context.payload["_config_path"] = config_path
    gherkin_context.payload["_cyt_mcp_enabled"] = False


@given("cursor MCP config has no cyt-mcp entry")
def given_empty_mcp(gherkin_context: GherkinContext, tmp_path: Path) -> None:
    mcp_path = tmp_path / "mcp.json"
    mcp_path.write_text(json.dumps({"mcpServers": {}}) + "\n", encoding="utf-8")
    gherkin_context.payload["_mcp_path"] = mcp_path
    gherkin_context.payload["_mcp_before"] = mcp_path.read_text(encoding="utf-8")


@when("cyt-client handles sessionStart")
def when_session_start(gherkin_context: GherkinContext, monkeypatch: pytest.MonkeyPatch) -> None:
    _run_pairing_scenario(gherkin_context, monkeypatch, event="sessionStart")


@when("cyt-client handles UserPromptSubmit")
def when_user_prompt(gherkin_context: GherkinContext, monkeypatch: pytest.MonkeyPatch) -> None:
    _run_pairing_scenario(gherkin_context, monkeypatch, event="UserPromptSubmit")


def _run_pairing_scenario(
    gherkin_context: GherkinContext,
    monkeypatch: pytest.MonkeyPatch,
    *,
    event: str,
) -> None:
    from cyt_client.cli import main

    config_path = gherkin_context.payload["_config_path"]
    mcp_path = gherkin_context.payload["_mcp_path"]
    monkeypatch.setattr("cyt_client.config.resolve_config_path", lambda: config_path)
    monkeypatch.setattr(
        "cyt_client.config.tools_from_includes_cyt_mcp",
        lambda: bool(gherkin_context.payload.get("_cyt_mcp_enabled")),
    )
    monkeypatch.setattr("cyt_client.agent.infer_harness_agent", lambda _payload: "cursor")
    monkeypatch.setattr("cyt_client.pairing._AGENT_MCP_PATHS", {"cursor": mcp_path})
    monkeypatch.setattr(
        "cyt_client.pairing._AGENT_HOOK_PATHS",
        {"cursor": mcp_path.parent / "hooks.json"},
    )

    payload = {
        "hook_event_name": event,
        "session_id": "pair-session",
        "cursor_version": "1.0",
    }
    stdin = json.dumps(payload).encode()

    if event == "UserPromptSubmit":
        with patch("cyt_client.cli.resolve_hook_url", return_value=None):
            with patch("sys.stdin.buffer.read", return_value=stdin):
                main()
    else:
        with patch("sys.stdin.buffer.read", return_value=stdin):
            main()

    gherkin_context.payload["_mcp_after"] = mcp_path.read_text(encoding="utf-8")


@then("cursor mcp.json should contain a cyt-mcp server entry")
def then_mcp_has_cyt_mcp(gherkin_context: GherkinContext) -> None:
    data = json.loads(gherkin_context.payload["_mcp_after"])
    assert "cyt-mcp" in data.get("mcpServers", {})


@then("cursor mcp.json should not be modified")
def then_mcp_unchanged(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload["_mcp_after"] == gherkin_context.payload["_mcp_before"]


@given("no session log file")
def given_no_session_log_file(gherkin_context: GherkinContext) -> None:
    gherkin_context.payload = {
        "session_id": "session-1",
    }


@given("a session log with turn entry only")
def given_turn_only_session(gherkin_context: GherkinContext, tmp_path: Path) -> None:
    log_path = tmp_path / "session.jsonl"
    _write_session_log(
        log_path,
        [
            {
                "kind": "turn",
                "key": "turn:1",
                "prompt": "hello",
                "assistant": "hi",
            },
        ],
    )
    gherkin_context.payload = {
        "log_path": log_path,
        "session_id": "session-1",
    }


@given("a preToolUse payload calling Shell")
def given_shell_payload(gherkin_context: GherkinContext) -> None:
    gherkin_context.payload["hook_payload"] = {
        "hook_event_name": "preToolUse",
        "session_id": gherkin_context.payload.get("session_id", "session-1"),
        "tool_name": "Shell",
        "tool_input": {"command": "echo hi"},
    }


@given("an empty session log")
def given_empty_session_log(gherkin_context: GherkinContext, tmp_path: Path) -> None:
    log_path = tmp_path / "session.jsonl"
    log_path.write_text("", encoding="utf-8")
    gherkin_context.payload = {
        "log_path": log_path,
        "session_id": "session-1",
    }


@given(
    "a preToolUse payload calling cyt-mcp_search with tool_name codebase-memory-mcp_search_graph",
)
def given_search_tool_payload(gherkin_context: GherkinContext) -> None:
    gherkin_context.payload["hook_payload"] = {
        "hook_event_name": "preToolUse",
        "session_id": gherkin_context.payload["session_id"],
        "tool_name": "cyt-mcp_search",
        "tool_input": {"tool_name": "codebase-memory-mcp_search_graph"},
    }


@given("a session log with only a search-resolved cyt_mcp tool entry")
def given_search_resolved_session(gherkin_context: GherkinContext, tmp_path: Path) -> None:
    log_path = tmp_path / "session.jsonl"
    _write_session_log(
        log_path,
        [
            {
                "kind": "tool",
                "key": "tool:cyt_mcp:codebase-memory-mcp_search_graph",
                "name": "codebase-memory-mcp_search_graph",
                "catalog": "cyt_mcp",
                "full": True,
                "source": "cyt-mcp_search",
                "input_schema": {
                    "type": "object",
                    "properties": {"project": {"type": "string"}},
                },
            },
        ],
    )
    gherkin_context.payload = {
        "log_path": log_path,
        "session_id": "session-1",
    }


@given("a preToolUse payload calling codebase-memory-mcp_search_graph")
def given_backend_search_graph_payload(gherkin_context: GherkinContext) -> None:
    gherkin_context.payload["hook_payload"] = {
        "hook_event_name": "preToolUse",
        "session_id": gherkin_context.payload["session_id"],
        "tool_name": "codebase-memory-mcp_search_graph",
        "tool_input": {"project": "demo"},
    }


@given("a preToolUse payload calling codebase-memory-mcp_query_graph")
def given_backend_query_graph_payload(gherkin_context: GherkinContext) -> None:
    gherkin_context.payload["hook_payload"] = {
        "hook_event_name": "preToolUse",
        "session_id": gherkin_context.payload["session_id"],
        "tool_name": "codebase-memory-mcp_query_graph",
        "tool_input": {"project": "demo", "query": "MATCH (n) RETURN n"},
    }


@given(parsers.parse("a {hook_event} payload for cyt-mcp_search with tool_name {tool_name}"))
def given_post_tool_payload(
    hook_event: str,
    tool_name: str,
    gherkin_context: GherkinContext,
) -> None:
    definition = {
        "name": tool_name,
        "inputSchema": {
            "type": "object",
            "properties": {"project": {"type": "string"}},
            "required": ["project"],
        },
        "description": "full search graph tool",
    }
    gherkin_context.payload["hook_payload"] = {
        "hook_event_name": hook_event,
        "session_id": gherkin_context.payload["session_id"],
        "tool_name": "cyt-mcp_search",
        "tool_input": {"tool_name": tool_name},
        "tool_result": json.dumps(definition),
    }


@when("cyt-client handles post-tool capture")
def when_post_tool_capture(
    gherkin_context: GherkinContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cyt_client.session_capture import persist_cyt_mcp_search_result

    log_path = gherkin_context.payload["log_path"]
    monkeypatch.setattr(
        "cyt_client.session_capture.session_log_path",
        lambda _payload: log_path,
    )
    persist_cyt_mcp_search_result(gherkin_context.payload["hook_payload"])
    gherkin_context.payload["session_entries"] = json.loads(
        log_path.read_text(encoding="utf-8").strip().splitlines()[-1],
    )


@then(parsers.parse("session log should contain a tool entry for {tool_name}"))
def then_session_has_tool(tool_name: str, gherkin_context: GherkinContext) -> None:
    entry = gherkin_context.payload["session_entries"]
    assert entry["name"] == tool_name


@then("tool entry catalog should be cyt_mcp")
def then_tool_catalog_cyt_mcp(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload["session_entries"]["catalog"] == "cyt_mcp"


@then("tool entry full should be true")
def then_tool_full(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload["session_entries"]["full"] is True


@then("tool entry source should be cyt-mcp_search")
def then_tool_source_search(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload["session_entries"]["source"] == "cyt-mcp_search"


@given("a beforeSubmitPrompt payload with prompt and transcript")
def given_turn_payload(gherkin_context: GherkinContext) -> None:
    gherkin_context.payload["hook_payload"] = {
        "hook_event_name": "beforeSubmitPrompt",
        "session_id": gherkin_context.payload["session_id"],
        "prompt": "find callers of main",
        "cyt_transcript": [
            {
                "role": "assistant",
                "message": {"content": [{"type": "text", "text": "I will search."}]},
            },
        ],
    }


@when("cyt-client persists turn to session log")
def when_persist_turn(gherkin_context: GherkinContext, monkeypatch: pytest.MonkeyPatch) -> None:
    from cyt_client.session_capture import persist_turn_to_session_log

    log_path = gherkin_context.payload["log_path"]
    monkeypatch.setattr(
        "cyt_client.session_capture.session_log_path",
        lambda _payload: log_path,
    )
    persist_turn_to_session_log(gherkin_context.payload["hook_payload"])
    gherkin_context.payload["session_entries"] = json.loads(
        log_path.read_text(encoding="utf-8").strip().splitlines()[-1],
    )


@then("session log should contain a turn entry with matching prompt")
def then_turn_entry(gherkin_context: GherkinContext) -> None:
    entry = gherkin_context.payload["session_entries"]
    assert entry["kind"] == "turn"
    assert entry["prompt"] == "find callers of main"


@given("a session log with a turn entry and a full cyt_mcp tool entry")
def given_turn_and_tool_session(gherkin_context: GherkinContext, tmp_path: Path) -> None:
    log_path = tmp_path / "session.jsonl"
    _write_session_log(
        log_path,
        [
            {
                "kind": "turn",
                "key": "turn:1",
                "prompt": "analyze graph",
                "assistant": "checking tools",
            },
            {
                "kind": "tool",
                "key": "tool:cyt_mcp:codebase-memory-mcp_search_graph",
                "name": "codebase-memory-mcp_search_graph",
                "catalog": "cyt_mcp",
                "full": True,
                "input_schema": {"type": "object", "properties": {"project": {"type": "string"}}},
                "description": "search graph",
            },
        ],
    )
    gherkin_context.payload = {"log_path": log_path}


@when("combined session text is built")
def when_combined_session_text(gherkin_context: GherkinContext) -> None:
    from cyt.injection.session_log import SessionLogIndex, combined_session_text
    from cyt_client.sessions import read_session_log_file

    _agent, entries = read_session_log_file(gherkin_context.payload["log_path"])
    index = SessionLogIndex(entries=tuple(entries))
    gherkin_context.payload["combined_text"] = combined_session_text("", index)


@then("corpus should include turn prompt text")
def then_corpus_has_turn(gherkin_context: GherkinContext) -> None:
    assert "analyze graph" in gherkin_context.payload["combined_text"]
