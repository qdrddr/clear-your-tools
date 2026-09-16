"""Tests for injection header pre-exposure detection."""

from __future__ import annotations

from typing import Any

from cyt.injection.header_pre_exposed import (
    agent_tools_intro_pre_exposed,
    agent_tools_path_pre_exposed,
    cyt_mcp_note_pre_exposed,
    intro_text_pre_exposed,
)
from cyt.tools.inject import _AGENT_TOOLS_DESCRIPTION_BASE
from cyt.tools.source_inject import _CYT_MCP_WORKSPACE_NOTE


def test_intro_text_pre_exposed_inner_text() -> None:
    intro = _AGENT_TOOLS_DESCRIPTION_BASE
    corpus = f"<agent-tools>\n{intro}\n<tool></tool>\n</agent-tools>"
    assert intro_text_pre_exposed(corpus, intro) is True


def test_intro_text_pre_exposed_legacy_attribute() -> None:
    intro = _AGENT_TOOLS_DESCRIPTION_BASE
    corpus = f"<agent-tools description='{intro}'>\n</agent-tools>"
    assert agent_tools_intro_pre_exposed(corpus, intro) is True


def test_cyt_mcp_note_pre_exposed() -> None:
    corpus = f"<cyt-mcp>\n{_CYT_MCP_WORKSPACE_NOTE}\n</cyt-mcp>"
    assert cyt_mcp_note_pre_exposed(corpus, _CYT_MCP_WORKSPACE_NOTE) is True


def test_intro_not_pre_exposed_on_empty_corpus() -> None:
    assert intro_text_pre_exposed("", _AGENT_TOOLS_DESCRIPTION_BASE) is False


def test_intro_not_pre_exposed_from_bare_substring_outside_agent_tools() -> None:
    intro = _AGENT_TOOLS_DESCRIPTION_BASE
    assert intro_text_pre_exposed(intro, intro) is False


def test_cyt_mcp_workspace_roots_included_when_note_pre_exposed() -> None:
    from cyt.tools.source_inject import format_cyt_mcp_source_section

    tool = {
        "name": "demo_tool",
        "description": "Demo",
        "input_schema": {"type": "object", "properties": {}},
        "cyt_catalog_scope": "user",
        "cyt_injection_tier": "t3",
    }
    other = {
        "name": "other_tool",
        "description": "Other",
        "input_schema": {"type": "object", "properties": {}},
        "cyt_catalog_scope": "user",
        "cyt_injection_tier": "t3",
    }
    prior = format_cyt_mcp_source_section(
        [tool],
        workspace_paths=["/tmp/a", "/tmp/b"],
    )
    section = format_cyt_mcp_source_section(
        [other],
        workspace_paths=["/tmp/a", "/tmp/b"],
        session_text=prior,
    )
    assert "pre-filtered tool definitions" not in section
    assert "<workspace_roots>" in section


def test_format_session_text_includes_rules_injection_for_cyt_mcp_pre_exposure() -> None:
    from cyt.injection.pre_exposure_context import PreExposureContext
    from cyt.injection.tier_legend import TOOL_TIER_LEGEND
    from cyt.tools.hook import _format_session_text
    from cyt.tools.source_inject import format_cyt_mcp_source_section

    tool = {
        "name": "demo_tool",
        "description": "Demo",
        "input_schema": {"type": "object", "properties": {}},
        "cyt_catalog_scope": "user",
        "cyt_injection_tier": "t3",
    }
    other = {
        "name": "other_tool",
        "description": "Other",
        "input_schema": {"type": "object", "properties": {}},
        "cyt_catalog_scope": "user",
        "cyt_injection_tier": "t3",
    }
    prior_rules = format_cyt_mcp_source_section([tool])
    ctx = PreExposureContext.from_entries(payload_text="follow-up question", entries=[])
    corpus = _format_session_text({"cyt_rules_injection": prior_rules}, ctx)
    section = format_cyt_mcp_source_section([other], session_text=corpus)
    assert TOOL_TIER_LEGEND not in section
    assert "other_tool" in section


def test_agent_tools_path_pre_exposed_open_tag() -> None:
    path = "/tmp/project"
    corpus = f"<agent-tools path='{path}'>\n<tool></tool>\n</agent-tools>"
    assert agent_tools_path_pre_exposed(corpus, path) is True


def test_agent_tools_path_pre_exposed_xml_escaped_path() -> None:
    from cyt.tools.inject import _xml_single_quoted_attr

    path = "/tmp/user's project"
    corpus = f"<agent-tools path='{_xml_single_quoted_attr(path)}'>\n</agent-tools>"
    assert agent_tools_path_pre_exposed(corpus, path) is True


def test_agent_tools_path_pre_exposed_not_from_workspace_item() -> None:
    path = "/tmp/project"
    corpus = f"<workspace_roots>\n<item path='{path}'/>\n</workspace_roots>"
    assert agent_tools_path_pre_exposed(corpus, path) is False


def test_agent_tools_path_not_pre_exposed_when_different() -> None:
    corpus = "<agent-tools path='/tmp/other'>\n</agent-tools>"
    assert agent_tools_path_pre_exposed(corpus, "/tmp/project") is False


def test_agent_tools_path_not_pre_exposed_on_empty_corpus() -> None:
    assert agent_tools_path_pre_exposed("", "/tmp/project") is False


def test_intro_reappears_after_compaction_slice() -> None:
    from cyt.injection.pre_exposure_context import PreExposureContext

    intro = _AGENT_TOOLS_DESCRIPTION_BASE
    pre_compaction: dict[str, Any] = {
        "kind": "turn",
        "prompt": f"<agent-tools>\n{intro}\n</agent-tools>",
        "assistant": "",
    }
    entries: list[dict[str, Any]] = [
        pre_compaction,
        {"kind": "compaction", "key": "compaction", "payload": {}},
    ]
    ctx = PreExposureContext.from_entries(payload_text="new question", entries=entries)
    assert agent_tools_intro_pre_exposed(ctx.combined_text, intro) is False
