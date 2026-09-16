"""Tests for rules-file refresh bypass of hook pre-exposure."""

from __future__ import annotations

import tempfile
from pathlib import Path

from cyt.injection.pre_exposure_context import PreExposureContext
from cyt.injection.pre_exposure_pipeline import gate_and_filter_tools
from cyt.injection.rules_refresh import bypass_injection_pre_exposure
from cyt.tools.inject import format_tool_item
from cyt.tools.source_inject import format_cyt_mcp_source_section, format_multi_source_agent_tools
from cyt_client.rules_file import (
    build_rules_mdc_placeholder,
    is_rules_placeholder_body,
    is_substantive_rules_injection,
    read_prior_rules_injection_for_hook,
    rules_injection_needs_format_refresh,
)


def _sample_tool(name: str = "demo_tool") -> dict:
    return {
        "name": name,
        "description": "Demo",
        "input_schema": {"type": "object", "properties": {}},
        "cyt_catalog_scope": "user",
        "cyt_injection_tier": "t3",
    }


def test_is_rules_placeholder_body() -> None:
    body = build_rules_mdc_placeholder().split("---", 2)[-1].strip()
    assert is_rules_placeholder_body(body)
    assert not rules_injection_needs_format_refresh(body)


def test_sync_cursor_rules_file_placeholder_when_both_domains_empty_wrappers() -> None:
    from cyt_client.rules_file import (
        _RULES_PLACEHOLDER_BODY,
        build_rules_mdc,
        rules_file_path,
        sync_cursor_rules_file,
    )

    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        path = rules_file_path(workspace)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(build_rules_mdc("<agent-tools>existing</agent-tools>"), encoding="utf-8")
        empty_both = "<agent-tools>\n</agent-tools>\n\n<agent-skills>\n</agent-skills>"
        sync_cursor_rules_file(workspace, empty_both)
        body = path.read_text(encoding="utf-8").split("---", 2)[-1].strip()
        assert body == _RULES_PLACEHOLDER_BODY


def test_is_substantive_rules_injection_rejects_empty_wrappers() -> None:
    assert is_substantive_rules_injection("<agent-tools>\n</agent-tools>") is False
    assert is_substantive_rules_injection("<agent-skills>\n</agent-skills>") is False
    assert (
        is_substantive_rules_injection(
            "<agent-tools>\n</agent-tools>\n\n<agent-skills>\n</agent-skills>",
        )
        is False
    )
    assert (
        is_substantive_rules_injection("<agent-tools>\n<tool name='x'></tool>\n</agent-tools>")
        is True
    )


def test_rules_injection_needs_format_refresh_legacy_agent_tools_attribute() -> None:
    legacy = (
        "<agent-tools description='Pruned MCP tool definitions below'>\n"
        "<cyt-mcp>\n<tool name='demo'>{'input_schema':{}}\n</tool>\n</cyt-mcp>\n"
        "</agent-tools>"
    )
    assert rules_injection_needs_format_refresh(legacy) is True


def test_rules_injection_needs_format_refresh_flat_cyt_mcp() -> None:
    legacy = (
        "<agent-tools>\nPruned MCP tool definitions below\n"
        "<cyt-mcp>\n<tool name='demo'>{'input_schema':{}}\n</tool>\n</cyt-mcp>\n"
        "</agent-tools>"
    )
    assert rules_injection_needs_format_refresh(legacy) is True


def test_rules_injection_needs_format_refresh_header_only_cyt_mcp() -> None:
    stale = (
        "<agent-tools>\nPruned MCP tool definitions below\n"
        "<cyt-mcp>\nTool tiers are grouped in <tier_tN>…</tier_tN> wrappers.\n"
        "No relevant cyt-mcp tools matched this prompt.\n"
        "</cyt-mcp>\n</agent-tools>"
    )
    assert rules_injection_needs_format_refresh(stale) is True


def test_rules_injection_does_not_refresh_new_scope_layout() -> None:
    modern = (
        "<agent-tools>\nPruned MCP tool definitions below\n"
        "<cyt-mcp>\n<cyt-mcp-usr>\n<tool name='demo'>{'input_schema':{}}\n</tool>\n"
        "</cyt-mcp-usr>\n</cyt-mcp>\n</agent-tools>"
    )
    assert rules_injection_needs_format_refresh(modern) is False


def test_bypass_injection_pre_exposure_from_payload_flag() -> None:
    assert bypass_injection_pre_exposure({"cyt_force_rules_refresh": True}) is True
    assert bypass_injection_pre_exposure({"cyt_force_rules_refresh": False}) is False


def test_bypass_injection_pre_exposure_skipped_when_session_has_tools() -> None:
    ctx = PreExposureContext.from_entries(
        payload_text="follow-up",
        entries=[
            {
                "kind": "tool",
                "key": "tool:cyt_mcp:demo_tool",
                "hash": "abc",
                "full": False,
                "name": "demo_tool",
            },
        ],
    )
    assert bypass_injection_pre_exposure({"cyt_force_rules_refresh": True}, ctx) is False


def test_gate_and_filter_tools_bypasses_pre_exposure_when_rules_refresh() -> None:
    tool = _sample_tool()
    fragment = format_tool_item(tool)
    ctx = PreExposureContext.from_entries(
        payload_text=fragment,
        entries=[],
    )
    payload = {"cyt_force_rules_refresh": True}
    gated, _logs, _ = gate_and_filter_tools(
        [tool],
        config={},
        ctx=ctx,
        source_id="cyt_mcp",
        payload=payload,
    )
    assert gated == [tool]


def test_format_multi_source_emits_intro_after_rules_refresh() -> None:
    wrapped = format_multi_source_agent_tools(
        {"cyt_mcp": format_cyt_mcp_source_section([_sample_tool("other_tool")])},
        session_text="",
    )
    assert "Pruned MCP tool definitions below" in wrapped
    assert "other_tool" in wrapped


def test_read_prior_rules_injection_for_hook_placeholder(tmp_path: Path) -> None:
    workspace = Path(tmp_path)
    rules_path = workspace / ".cursor" / "rules" / "cyt-injection.mdc"
    rules_path.parent.mkdir(parents=True)
    rules_path.write_text(build_rules_mdc_placeholder(), encoding="utf-8")
    injection, force_refresh = read_prior_rules_injection_for_hook(workspace)
    assert injection == ""
    assert force_refresh is True


def _write_session_log(workspace: Path, conversation_id: str, entries: list[dict]) -> None:
    from cyt_client.sessions import append_session_log, session_log_path

    payload = {
        "conversation_id": conversation_id,
        "workspace_roots": [str(workspace)],
        "cyt_agent": "cursor",
    }
    path = session_log_path(payload)
    assert path is not None
    path.parent.mkdir(parents=True, exist_ok=True)
    append_session_log(path, entries, agent="cursor")


def test_read_prior_rules_injection_for_hook_placeholder_with_session_tools(
    tmp_path: Path,
) -> None:
    """Pre-exposure skip: placeholder + session tools + completed turn → no force refresh."""
    workspace = Path(tmp_path)
    rules_path = workspace / ".cursor" / "rules" / "cyt-injection.mdc"
    rules_path.parent.mkdir(parents=True)
    rules_path.write_text(build_rules_mdc_placeholder(), encoding="utf-8")
    conversation_id = "conv-placeholder-session"
    _write_session_log(
        workspace,
        conversation_id,
        [
            {"kind": "turn", "key": "turn:1", "prompt": "first", "assistant": ""},
            {"kind": "turn", "key": "turn:2", "prompt": "second", "assistant": "done"},
            {
                "kind": "tool",
                "key": "tool:cyt_mcp:demo_tool",
                "hash": "abc",
                "full": True,
                "name": "demo_tool",
            },
        ],
    )
    payload = {
        "conversation_id": conversation_id,
        "workspace_roots": [str(workspace)],
        "cyt_agent": "cursor",
    }
    injection, force_refresh = read_prior_rules_injection_for_hook(workspace, payload)
    assert injection == ""
    assert force_refresh is False


def test_read_prior_rules_injection_for_hook_placeholder_first_turn_still_refreshes(
    tmp_path: Path,
) -> None:
    """First prompt after restart: tools without completed turn → still force refresh."""
    workspace = Path(tmp_path)
    rules_path = workspace / ".cursor" / "rules" / "cyt-injection.mdc"
    rules_path.parent.mkdir(parents=True)
    rules_path.write_text(build_rules_mdc_placeholder(), encoding="utf-8")
    conversation_id = "conv-first-turn"
    _write_session_log(
        workspace,
        conversation_id,
        [
            {
                "kind": "tool",
                "key": "tool:cyt_mcp:demo_tool",
                "hash": "abc",
                "full": False,
                "name": "demo_tool",
            },
        ],
    )
    payload = {
        "conversation_id": conversation_id,
        "workspace_roots": [str(workspace)],
        "cyt_agent": "cursor",
    }
    injection, force_refresh = read_prior_rules_injection_for_hook(workspace, payload)
    assert injection == ""
    assert force_refresh is True


def test_placeholder_skip_and_first_prompt_both_preserve_hook_gating() -> None:
    """Client force-refresh flag and hook bypass must agree for both lifecycle cases."""
    tool = _sample_tool()
    fragment = format_tool_item(tool)

    # Scenario A: new session / first prompt — force refresh, bypass pre-exposure.
    fresh_ctx = PreExposureContext.from_entries(payload_text="locate BM25", entries=[])
    assert bypass_injection_pre_exposure({"cyt_force_rules_refresh": True}, fresh_ctx) is True

    # Scenario B: post skip — client suppresses refresh; hook also refuses bypass.
    follow_up_ctx = PreExposureContext.from_entries(
        payload_text=fragment,
        entries=[
            {
                "kind": "tool",
                "key": "tool:cyt_mcp:demo_tool",
                "hash": "abc",
                "full": True,
                "name": "demo_tool",
            },
            {"kind": "turn", "key": "turn:1", "prompt": "q", "assistant": "a"},
        ],
    )
    assert bypass_injection_pre_exposure({"cyt_force_rules_refresh": False}, follow_up_ctx) is False
    assert bypass_injection_pre_exposure({"cyt_force_rules_refresh": True}, follow_up_ctx) is False
    gated, _logs, _ = gate_and_filter_tools(
        [tool],
        config={},
        ctx=follow_up_ctx,
        source_id="cyt_mcp",
        payload={"cyt_force_rules_refresh": True},
    )
    assert gated == []


def test_read_prior_rules_injection_for_hook_legacy_format(tmp_path: Path) -> None:
    workspace = Path(tmp_path)
    rules_path = workspace / ".cursor" / "rules" / "cyt-injection.mdc"
    rules_path.parent.mkdir(parents=True)
    rules_path.write_text(
        "---\ndescription: x\nalwaysApply: true\n---\n\n"
        "<agent-tools description='legacy'>\n<cyt-mcp>\n<tool name='a'></tool>\n</cyt-mcp>\n</agent-tools>\n",
        encoding="utf-8",
    )
    injection, force_refresh = read_prior_rules_injection_for_hook(workspace)
    assert injection == ""
    assert force_refresh is True


def test_session_start_does_not_wipe_substantive_rules(tmp_path: Path) -> None:
    from cyt_client.cli import _sync_cursor_rules_for_lifecycle
    from cyt_client.rules_file import build_rules_mdc, read_cursor_rules_injection

    workspace = Path(tmp_path)
    substantive = (
        "<agent-tools>\nPruned MCP tool definitions below\n"
        "<cyt-mcp>\n<tool name='demo'>{'input_schema':{}}\n</tool>\n</cyt-mcp>\n</agent-tools>"
    )
    rules_path = workspace / ".cursor" / "rules" / "cyt-injection.mdc"
    rules_path.parent.mkdir(parents=True)
    rules_path.write_text(build_rules_mdc(substantive), encoding="utf-8")
    payload = {"workspace_roots": [str(workspace)], "cyt_agent": "cursor"}
    _sync_cursor_rules_for_lifecycle(payload)
    assert read_cursor_rules_injection(workspace) == substantive


def test_read_prior_rules_injection_for_hook_missing_file(tmp_path: Path) -> None:
    workspace = Path(tmp_path)
    injection, force_refresh = read_prior_rules_injection_for_hook(workspace)
    assert injection == ""
    assert force_refresh is True


def test_workspace_path_string_reads_nested_payload() -> None:
    from cyt_client.rules_file import workspace_path_string, workspace_root_from_payload

    payload = {
        "hook_event_name": "beforeSubmitPrompt",
        "payload": {
            "workspace_roots": ["/c:/Users/me/git/clear-your-tools"],
            "prompt": "hello",
        },
    }
    assert workspace_path_string(payload) is not None
    assert workspace_root_from_payload(payload) == Path("C:/Users/me/git/clear-your-tools")
