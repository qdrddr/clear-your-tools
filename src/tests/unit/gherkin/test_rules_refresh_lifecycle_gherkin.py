"""Gherkin steps for rules refresh vs pre-exposure skip lifecycle compatibility."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, scenarios, then, when

from cyt.injection.pre_exposure_context import PreExposureContext
from cyt.injection.pre_exposure_pipeline import gate_and_filter_tools
from cyt.injection.rules_refresh import bypass_injection_pre_exposure
from cyt.injection.session_log_build import build_session_state_entry, build_tool_log_entry
from cyt.tools.inject import format_tool_item
from cyt_client.rules_file import (
    build_rules_mdc_placeholder,
    is_substantive_rules_injection,
    read_cursor_rules_injection,
    read_prior_rules_injection_for_hook,
    reset_cursor_rules_file_to_placeholder,
    sync_cursor_rules_file,
)
from cyt_client.sessions import append_session_log, read_session_log_file, session_log_path
from cyt_client.transcript import enrich_hook_payload
from tests.unit.gherkin.conftest import GherkinContext

FEATURES = Path(__file__).resolve().parent / "features" / "rules_refresh_lifecycle.feature"
scenarios(str(FEATURES))

pytestmark = pytest.mark.gherkin

_SESSION_ID = "rules-refresh-sess"
_PROMPT = "Locate primary code implementing BM25, use MCP: codebase-memory, semble, graphify."


def _demo_tool() -> dict[str, Any]:
    return {
        "name": "fff_grep",
        "description": "grep files",
        "input_schema": {
            "type": "object",
            "properties": {"pattern": {"type": "string"}},
        },
        "cyt_catalog_scope": "user",
        "cyt_catalog_source": "cyt_mcp",
        "cyt_injection_tier": "t3",
    }


def _base_payload(workspace: Path, *, prompt: str = _PROMPT) -> dict[str, Any]:
    return {
        "hook_event_name": "beforeSubmitPrompt",
        "prompt": prompt,
        "conversation_id": _SESSION_ID,
        "workspace_roots": [str(workspace)],
        "cyt_agent": "cursor",
    }


def _enriched_payload(
    workspace: Path,
    *,
    force_rules_refresh: bool,
    prior_rules_injection: str = "",
    prompt: str = _PROMPT,
    assistant: str = "",
) -> dict[str, Any]:
    payload = _base_payload(workspace, prompt=prompt)
    if assistant:
        payload["conversation"] = [{"role": "assistant", "content": assistant}]
    raw = enrich_hook_payload(
        json.dumps(payload).encode(),
        rules_injection=prior_rules_injection,
        force_rules_refresh=force_rules_refresh,
    )
    enriched = json.loads(raw)
    assert isinstance(enriched, dict)
    return enriched


def _hook_gate(
    payload: dict[str, Any],
    *,
    session_entries: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[str] | None]:
    ctx = PreExposureContext.from_entries(
        payload_text=str(payload.get("prompt") or ""),
        entries=session_entries,
        agent="cursor",
    )
    return gate_and_filter_tools(
        [_demo_tool()],
        config={"pruning": {"tools": {"enabled": True}}},
        ctx=ctx,
        source_id="cyt_mcp",
        payload=payload,
    )


def _append_turn(path: Path, *, prompt: str, assistant: str) -> None:
    append_session_log(
        path,
        [
            {
                "kind": "turn",
                "key": f"turn:{hash(prompt)}",
                "prompt": prompt,
                "assistant": assistant,
            },
        ],
        agent="cursor",
    )


def _run_submit_cycle(
    workspace: Path,
    *,
    prompt: str = _PROMPT,
    assistant: str = "",
    session_entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Simulate one beforeSubmitPrompt client→hook cycle without HTTP."""
    payload = _base_payload(workspace, prompt=prompt)
    prior_rules, force_refresh = read_prior_rules_injection_for_hook(workspace, payload)
    enriched = _enriched_payload(
        workspace,
        force_rules_refresh=force_refresh,
        prior_rules_injection=prior_rules,
        prompt=prompt,
        assistant=assistant,
    )

    path = session_log_path(enriched)
    assert path is not None
    if path.is_file():
        _agent, session_entries = read_session_log_file(path)
    else:
        session_entries = list(session_entries or [])

    ctx = PreExposureContext.from_entries(
        payload_text=prompt,
        entries=session_entries,
        agent="cursor",
    )
    bypass = bypass_injection_pre_exposure(enriched, ctx)
    gated, log_entries, _ = _hook_gate(enriched, session_entries=session_entries)

    if path.parent.exists() or session_entries:
        path.parent.mkdir(parents=True, exist_ok=True)
    _append_turn(path, prompt=prompt, assistant=assistant or f"response to {prompt[:20]}")

    if gated:
        append_session_log(path, log_entries, agent="cursor")
        append_session_log(
            path,
            [build_session_state_entry(tools_inject_enabled=True)],
            agent="cursor",
        )
        fragment = format_tool_item(gated[0])
        sync_cursor_rules_file(workspace, f"<agent-tools>{fragment}</agent-tools>")
    else:
        if is_substantive_rules_injection(prior_rules):
            reset_cursor_rules_file_to_placeholder(workspace)
        elif not read_cursor_rules_injection(workspace).strip():
            reset_cursor_rules_file_to_placeholder(workspace)

    return {
        "force_refresh": force_refresh,
        "bypass": bypass,
        "gated_count": len(gated),
        "prior_rules": prior_rules,
    }


@given("a Cursor workspace with lifecycle placeholder rules")
def given_placeholder_workspace(tmp_path: Path, gherkin_context: GherkinContext) -> None:
    workspace = tmp_path / "project"
    rules_path = workspace / ".cursor" / "rules" / "cyt-injection.mdc"
    rules_path.parent.mkdir(parents=True)
    rules_path.write_text(build_rules_mdc_placeholder(), encoding="utf-8")
    gherkin_context.tmp_path = tmp_path
    gherkin_context.payload = {
        "workspace": workspace,
        "conversation_id": _SESSION_ID,
    }


@given("a new session with no injection log")
def given_empty_session(gherkin_context: GherkinContext) -> None:
    workspace: Path = gherkin_context.payload["workspace"]
    payload = _base_payload(workspace)
    path = session_log_path(payload)
    assert path is not None
    if path.is_file():
        path.unlink()
    gherkin_context.payload["hook_payload"] = payload


@given("a session log with demo_tool and three completed assistant turns")
def given_session_with_tools_and_turns(gherkin_context: GherkinContext) -> None:
    workspace: Path = gherkin_context.payload["workspace"]
    payload = _base_payload(workspace)
    path = session_log_path(payload)
    assert path is not None
    path.parent.mkdir(parents=True, exist_ok=True)
    tool = _demo_tool()
    tool_entry = build_tool_log_entry(tool, catalog="cyt_mcp", full=True)
    append_session_log(
        path,
        [
            {"kind": "turn", "key": "turn:1", "prompt": "one", "assistant": "a1"},
            {"kind": "turn", "key": "turn:2", "prompt": "two", "assistant": "a2"},
            {"kind": "turn", "key": "turn:3", "prompt": "three", "assistant": "a3"},
            tool_entry,
            build_session_state_entry(tools_inject_enabled=True),
            build_session_state_entry(tools_inject_enabled=True),
            build_session_state_entry(tools_inject_enabled=True),
        ],
        agent="cursor",
    )
    gherkin_context.payload["hook_payload"] = payload


@given("a BM25 locate prompt for demo_tool")
def given_bm25_prompt(gherkin_context: GherkinContext) -> None:
    gherkin_context.prompt = _PROMPT


@given("substantive rules synced before sessionStart")
def given_substantive_rules_before_session_start(gherkin_context: GherkinContext) -> None:
    from cyt_client.sessions import append_session_log, session_log_path

    workspace: Path = gherkin_context.payload["workspace"]
    substantive = (
        "<agent-tools>\nPruned MCP tool definitions below\n"
        "<cyt-mcp>\n<tool name='fff_grep'>{'input_schema':{}}\n</tool>\n</cyt-mcp>\n</agent-tools>"
    )
    sync_cursor_rules_file(workspace, substantive)
    payload = {
        "hook_event_name": "sessionStart",
        "conversation_id": _SESSION_ID,
        "workspace_roots": [str(workspace)],
        "cyt_agent": "cursor",
    }
    path = session_log_path(payload)
    assert path is not None
    path.parent.mkdir(parents=True, exist_ok=True)
    append_session_log(
        path,
        [build_tool_log_entry(_demo_tool(), catalog="cyt_mcp", full=True)],
        agent="cursor",
    )
    gherkin_context.payload["substantive_rules"] = substantive
    gherkin_context.payload["session_start_payload"] = payload


@when("the client resolves rules refresh for beforeSubmitPrompt")
def when_client_resolves_rules_refresh(gherkin_context: GherkinContext) -> None:
    workspace: Path = gherkin_context.payload["workspace"]
    payload = gherkin_context.payload.get("hook_payload") or _base_payload(workspace)
    prior_rules, force_refresh = read_prior_rules_injection_for_hook(workspace, payload)
    enriched = _enriched_payload(
        workspace,
        force_rules_refresh=force_refresh,
        prior_rules_injection=prior_rules,
    )
    path = session_log_path(enriched)
    entries: list[dict[str, Any]] = []
    if path is not None and path.is_file():
        _agent, entries = read_session_log_file(path)
    ctx = PreExposureContext.from_entries(
        payload_text=str(payload.get("prompt") or ""),
        entries=entries,
        agent="cursor",
    )
    gherkin_context.payload["force_refresh"] = force_refresh
    gherkin_context.payload["prior_rules"] = prior_rules
    gherkin_context.payload["enriched_payload"] = enriched
    gherkin_context.payload["pre_exposure_ctx"] = ctx


@when("the hook runs first-prompt injection for demo_tool")
def when_hook_first_prompt_inject(gherkin_context: GherkinContext) -> None:
    workspace: Path = gherkin_context.payload["workspace"]
    enriched = gherkin_context.payload["enriched_payload"]
    gated, log_entries, _ = _hook_gate(enriched, session_entries=[])
    gherkin_context.payload["gated"] = gated
    if gated:
        path = session_log_path(enriched)
        assert path is not None
        path.parent.mkdir(parents=True, exist_ok=True)
        append_session_log(path, log_entries, agent="cursor")
        append_session_log(
            path,
            [build_session_state_entry(tools_inject_enabled=True)],
            agent="cursor",
        )
        fragment = format_tool_item(gated[0])
        sync_cursor_rules_file(workspace, f"<agent-tools>{fragment}</agent-tools>")


@when("the hook runs follow-up injection for demo_tool")
def when_hook_follow_up_inject(gherkin_context: GherkinContext) -> None:
    enriched = gherkin_context.payload["enriched_payload"]
    path = session_log_path(enriched)
    assert path is not None
    _agent, entries = read_session_log_file(path)
    gated, _logs, _ = _hook_gate(enriched, session_entries=entries)
    gherkin_context.payload["gated"] = gated


@when("five beforeSubmitPrompt cycles run through client and hook")
def when_five_submit_cycles(gherkin_context: GherkinContext) -> None:
    workspace: Path = gherkin_context.payload["workspace"]
    trace: list[dict[str, Any]] = []
    assistant = ""
    for index in range(5):
        result = _run_submit_cycle(
            workspace,
            prompt=_PROMPT,
            assistant=assistant,
        )
        trace.append({"iteration": index + 1, **result})
        assistant = f"assistant reply {index + 1}"
    gherkin_context.payload["cycle_trace"] = trace


@when("sessionStart lifecycle sync runs")
def when_session_start_sync(gherkin_context: GherkinContext) -> None:
    from cyt_client.cli import _sync_cursor_rules_for_lifecycle

    payload = gherkin_context.payload.get("session_start_payload")
    if payload is None:
        workspace: Path = gherkin_context.payload["workspace"]
        payload = {
            "hook_event_name": "sessionStart",
            "conversation_id": _SESSION_ID,
            "workspace_roots": [str(workspace)],
            "cyt_agent": "cursor",
        }
    _sync_cursor_rules_for_lifecycle(payload)


@then("cyt_force_rules_refresh should be true")
def then_force_refresh_true(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload["force_refresh"] is True


@then("cyt_force_rules_refresh should be false")
def then_force_refresh_false(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload["force_refresh"] is False


@then("hook pre-exposure bypass should be allowed")
def then_bypass_allowed(gherkin_context: GherkinContext) -> None:
    enriched = gherkin_context.payload["enriched_payload"]
    ctx = gherkin_context.payload["pre_exposure_ctx"]
    assert bypass_injection_pre_exposure(enriched, ctx) is True


@then("hook pre-exposure bypass should be blocked")
def then_bypass_blocked(gherkin_context: GherkinContext) -> None:
    enriched = gherkin_context.payload["enriched_payload"]
    ctx = gherkin_context.payload["pre_exposure_ctx"]
    assert bypass_injection_pre_exposure(enriched, ctx) is False


@then("hook should inject demo_tool without pre-exposure skip")
def then_hook_injects(gherkin_context: GherkinContext) -> None:
    gated = gherkin_context.payload["gated"]
    assert len(gated) == 1
    assert gated[0]["name"] == "fff_grep"


@then("hook should skip demo_tool injection")
def then_hook_skips(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload["gated"] == []


@then("the session log should contain a demo_tool entry")
def then_session_log_has_tool(gherkin_context: GherkinContext) -> None:
    workspace: Path = gherkin_context.payload["workspace"]
    path = session_log_path(_base_payload(workspace))
    assert path is not None
    _agent, entries = read_session_log_file(path)
    assert any(entry.get("kind") == "tool" and entry.get("name") == "fff_grep" for entry in entries)


@then("force refresh should only be true on the first iteration")
def then_force_refresh_first_only(gherkin_context: GherkinContext) -> None:
    trace = gherkin_context.payload["cycle_trace"]
    force_flags = [row["force_refresh"] for row in trace]
    assert force_flags[0] is True
    assert force_flags[1:] == [False, False, False, False]


@then("iterations four and five should skip injection without re-flapping")
def then_iterations_four_five_skip(gherkin_context: GherkinContext) -> None:
    trace = gherkin_context.payload["cycle_trace"]
    fourth = trace[3]
    fifth = trace[4]
    assert fourth["force_refresh"] is False
    assert fifth["force_refresh"] is False
    assert fourth["bypass"] is False
    assert fifth["bypass"] is False
    assert fourth["gated_count"] == 0
    assert fifth["gated_count"] == 0


@then("the Cursor rules file should retain substantive injection")
def then_rules_retain_substantive(gherkin_context: GherkinContext) -> None:
    workspace: Path = gherkin_context.payload["workspace"]
    expected = gherkin_context.payload["substantive_rules"]
    assert read_cursor_rules_injection(workspace) == expected
    assert is_substantive_rules_injection(expected)
