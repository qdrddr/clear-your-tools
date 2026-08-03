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
    log_path = gherkin_context.payload["log_path"]
    monkeypatch.setattr(
        "cyt_client.tool_gate.session_log_path",
        lambda _payload: log_path,
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
