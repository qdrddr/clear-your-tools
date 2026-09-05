"""Tests for injection header pre-exposure detection."""

from __future__ import annotations

from typing import Any

from cyt.injection.header_pre_exposed import (
    agent_tools_intro_pre_exposed,
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

    prior = format_cyt_mcp_source_section(
        [{"name": "demo_tool", "input_schema": {}, "cyt_catalog_scope": "user"}],
        workspace_paths=["/tmp/a", "/tmp/b"],
    )
    section = format_cyt_mcp_source_section(
        [{"name": "other_tool", "input_schema": {}, "cyt_catalog_scope": "user"}],
        workspace_paths=["/tmp/a", "/tmp/b"],
        session_text=prior,
    )
    assert "pre-filtered tool definitions" not in section
    assert "<workspace_roots>" in section


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
