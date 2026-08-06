"""Gherkin steps for Type-2 tool catalog gate."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from cyt.common.agents import AgentName
from cyt_client.sessions import append_tool_catalog_entries, read_session_log_file
from cyt_client.tool_gate import validate_pre_tool_call
from cyt_client.transcript import enrich_hook_payload
from tests.unit.gherkin.conftest import GherkinContext

FEATURES = Path(__file__).resolve().parent / "features" / "tool_catalog_gate.feature"
scenarios(str(FEATURES))

pytestmark = pytest.mark.gherkin


@given(parsers.parse("agent {agent}"))
def given_agent(agent: str, gherkin_context: GherkinContext) -> None:
    gherkin_context.agent = cast(AgentName, agent)


def _write_session(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(entry) for entry in entries) + "\n", encoding="utf-8")


def _cyt_mcp_catalog(tool_name: str, schema: dict, *, inject_enabled: bool = True) -> list[dict]:
    entries: list[dict] = [
        {
            "kind": "session_state",
            "key": "session_state:inject",
            "tools_inject_enabled": inject_enabled,
        },
        {
            "kind": "tool_catalog",
            "key": "tool_catalog:cyt_mcp",
            "catalog": "cyt_mcp",
            "hash": "hash-cyt-mcp",
            "tools": [
                {
                    "name": tool_name,
                    "input_schema": schema,
                },
            ],
        },
    ]
    return entries


@given("no session log file for preToolUse")
def given_no_session_log_for_gate(gherkin_context: GherkinContext) -> None:
    gherkin_context.payload = {
        "session_id": "session-1",
        "log_path": None,
    }


@given("a session log with tools inject disabled")
def given_skills_only(gherkin_context: GherkinContext, tmp_path: Path) -> None:
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


@given("a session log with tools inject disabled and no hallucination gate")
def given_skills_only_no_gate(gherkin_context: GherkinContext, tmp_path: Path) -> None:
    given_skills_only(gherkin_context, tmp_path)


@given("a session log with tools inject enabled and no Type-2 catalog")
def given_active_no_catalog(gherkin_context: GherkinContext, tmp_path: Path) -> None:
    log_path = tmp_path / "session.jsonl"
    _write_session(
        log_path,
        [
            {
                "kind": "session_state",
                "key": "session_state:inject",
                "tools_inject_enabled": True,
            },
        ],
    )
    gherkin_context.payload = {"log_path": log_path, "session_id": "session-1"}


@given(
    parsers.parse(
        "a Type-2 cyt_mcp catalog with tool {tool_name} path string",
    ),
)
def given_cyt_mcp_catalog_simple(
    tool_name: str,
    gherkin_context: GherkinContext,
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "session.jsonl"
    schema: dict = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
    }
    _write_session(log_path, _cyt_mcp_catalog(tool_name, schema))
    gherkin_context.payload = {"log_path": log_path, "session_id": "session-1"}


@given(
    parsers.parse(
        "a Type-2 cyt_mcp catalog with tool {tool_name} path string required",
    ),
)
def given_cyt_mcp_catalog_required(
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
    _write_session(log_path, _cyt_mcp_catalog(tool_name, schema))
    gherkin_context.payload = {"log_path": log_path, "session_id": "session-1"}


@given(
    parsers.parse(
        "a Type-2 mcpc catalog session {session} tool {tool_name} libraryName string query string",
    ),
)
def given_mcpc_catalog(
    session: str,
    tool_name: str,
    gherkin_context: GherkinContext,
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "session.jsonl"
    _write_session(
        log_path,
        [
            {
                "kind": "session_state",
                "key": "session_state:inject",
                "tools_inject_enabled": True,
            },
            {
                "kind": "tool_catalog",
                "key": "tool_catalog:mcpc",
                "catalog": "mcpc",
                "hash": "hash-mcpc",
                "tools": [
                    {
                        "name": tool_name,
                        "mcpc_session": session,
                        "input_schema": {
                            "type": "object",
                            "properties": {
                                "libraryName": {"type": "string"},
                                "query": {"type": "string"},
                            },
                        },
                    },
                ],
            },
        ],
    )
    gherkin_context.payload = {"log_path": log_path, "session_id": "session-1"}


@given(
    parsers.parse(
        "a Type-2 mcpc catalog session {session} tool {tool_name} libraryName string",
    ),
)
def given_mcpc_catalog_minimal(
    session: str,
    tool_name: str,
    gherkin_context: GherkinContext,
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "session.jsonl"
    _write_session(
        log_path,
        [
            {
                "kind": "session_state",
                "key": "session_state:inject",
                "tools_inject_enabled": True,
            },
            {
                "kind": "tool_catalog",
                "key": "tool_catalog:mcpc",
                "catalog": "mcpc",
                "hash": "hash-mcpc",
                "tools": [
                    {
                        "name": tool_name,
                        "mcpc_session": session,
                        "input_schema": {
                            "type": "object",
                            "properties": {"libraryName": {"type": "string"}},
                        },
                    },
                ],
            },
        ],
    )
    gherkin_context.payload = {"log_path": log_path, "session_id": "session-1"}


@given(parsers.parse("a Type-2 cyt_mcp catalog entry on disk hash {content_hash}"))
def given_catalog_on_disk(
    content_hash: str,
    gherkin_context: GherkinContext,
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "session.jsonl"
    entry = {
        "kind": "tool_catalog",
        "key": "tool_catalog:cyt_mcp",
        "catalog": "cyt_mcp",
        "hash": content_hash,
        "tools": [
            {
                "name": "demo_tool",
                "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
            },
        ],
    }
    _write_session(log_path, [entry])
    gherkin_context.payload = {"log_path": log_path, "entry": entry}


@when(parsers.parse("appending the same Type-2 cyt_mcp catalog entry hash {content_hash}"))
def when_append_same_catalog(content_hash: str, gherkin_context: GherkinContext) -> None:
    log_path = gherkin_context.payload["log_path"]
    entry = dict(gherkin_context.payload["entry"])
    entry["hash"] = content_hash
    append_tool_catalog_entries(Path(log_path), [entry])


@given(parsers.parse("a session jsonl with Type-2 cyt_mcp catalog tool {tool_name}"))
def given_jsonl_for_post(tool_name: str, gherkin_context: GherkinContext, tmp_path: Path) -> None:
    log_path = tmp_path / ".cursor" / "cyt" / "sessions" / "session-1.jsonl"
    _write_session(
        log_path,
        _cyt_mcp_catalog(
            tool_name,
            {"type": "object", "properties": {"x": {"type": "string"}}},
        ),
    )
    gherkin_context.payload = {
        "log_path": log_path,
        "session_id": "session-1",
        "cwd": str(tmp_path),
        "workspace_roots": [str(tmp_path)],
    }


@when("building hook POST session log payload")
def when_build_post_payload(gherkin_context: GherkinContext) -> None:
    payload = {
        "hook_event_name": "beforeSubmitPrompt",
        "session_id": gherkin_context.payload["session_id"],
        "cwd": gherkin_context.payload.get("cwd"),
        "workspace_roots": gherkin_context.payload.get("workspace_roots"),
        "cyt_agent": gherkin_context.agent,
    }
    enriched = json.loads(enrich_hook_payload(json.dumps(payload).encode()))
    gherkin_context.payload["enriched"] = enriched


@then("cyt_session_log should not contain tool_catalog kind")
def then_no_tool_catalog_in_post(gherkin_context: GherkinContext) -> None:
    enriched = gherkin_context.payload["enriched"]
    session_log = enriched.get("cyt_session_log")
    assert isinstance(session_log, list)
    assert not any(item.get("kind") == "tool_catalog" for item in session_log)


@then("tool_catalog_hashes should include tool_catalog:cyt_mcp")
def then_hashes_present(gherkin_context: GherkinContext) -> None:
    enriched = gherkin_context.payload["enriched"]
    hashes = enriched.get("tool_catalog_hashes")
    assert isinstance(hashes, dict)
    assert "tool_catalog:cyt_mcp" in hashes


@then(parsers.parse("session jsonl should have {count:d} tool_catalog lines"))
def then_catalog_line_count(count: int, gherkin_context: GherkinContext) -> None:
    log_path = Path(gherkin_context.payload["log_path"])
    _agent, entries = read_session_log_file(log_path)
    catalog_lines = [entry for entry in entries if entry.get("kind") == "tool_catalog"]
    assert len(catalog_lines) == count


def _patch_session_log_path(
    monkeypatch: pytest.MonkeyPatch,
    gherkin_context: GherkinContext,
) -> None:
    if "log_path" not in gherkin_context.payload:
        return
    log_path = gherkin_context.payload["log_path"]
    if log_path is None:
        monkeypatch.setattr(
            "cyt_client.tool_gate.session_log_path",
            lambda _payload: None,
        )
    else:
        monkeypatch.setattr(
            "cyt_client.tool_gate.session_log_path",
            lambda _payload: Path(log_path),
        )


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


@when(
    parsers.parse(
        "preToolUse validates cyt-mcp get-tool-definitions with empty args",
    ),
)
def when_validate_get_tool_definitions_empty(gherkin_context: GherkinContext) -> None:
    payload = {
        "hook_event_name": "preToolUse",
        "session_id": gherkin_context.payload.get("session_id", "session-1"),
        "tool_name": "cyt-mcp_get-tool-definitions",
        "cyt_agent": gherkin_context.agent,
    }
    validation = validate_pre_tool_call(payload)
    gherkin_context.payload["allowed"] = validation.allowed
    gherkin_context.payload["reason"] = validation.reason


_SHELL_COMMANDS = {
    "mcpc_shell_resolve_library_id": (
        'echo \'{"libraryName":"react","query":"hooks"}\' | mcpc @ctx7 tools-call resolve-library-id'
    ),
    "mcpc_shell_unknown_tool": "echo '{}' | mcpc @ctx7 tools-call unknown-tool",
    "mcpc_shell_read_file": 'echo \'{"path":"/tmp"}\' | mcpc @filesystem tools-call read_file',
}


@when(parsers.parse("preToolUse validates Shell command {command_key}"))
def when_validate_shell(command_key: str, gherkin_context: GherkinContext) -> None:
    command = _SHELL_COMMANDS.get(command_key, command_key)
    payload = {
        "hook_event_name": "preToolUse",
        "session_id": gherkin_context.payload.get("session_id", "session-1"),
        "tool_name": "Shell",
        "tool_input": {"command": command},
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
