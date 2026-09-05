"""Unit tests for cyt-mcp source injection."""

from __future__ import annotations

from cyt.tools.inject import _AGENT_TOOLS_DESCRIPTION_BASE
from cyt.tools.source_inject import (
    format_cyt_mcp_source_section,
    format_multi_source_agent_tools,
)


def _sample_tool(name: str, *, scope: str | None = None) -> dict:
    tool: dict = {
        "name": name,
        "description": f"Tool {name}",
        "input_schema": {"type": "object", "properties": {}},
    }
    if scope is not None:
        tool["cyt_catalog_scope"] = scope
    return tool


def test_format_cyt_mcp_source_section_wraps_tools_in_user_scope() -> None:
    tools = [_sample_tool("filesystem_read_file")]
    section = format_cyt_mcp_source_section(tools)
    assert "<cyt-mcp>" in section
    assert "<cyt-mcp-usr>" in section
    assert "<cyt-mcp-ws>" not in section
    assert "filesystem_read_file" in section


def test_format_cyt_mcp_source_section_workspace_only() -> None:
    tools = [_sample_tool("ws_tool", scope="workspace")]
    section = format_cyt_mcp_source_section(tools)
    assert "<cyt-mcp-ws>" in section
    assert "ws_tool" in section
    assert "<cyt-mcp-usr>" not in section


def test_format_cyt_mcp_source_section_both_scopes() -> None:
    tools = [
        _sample_tool("usr_tool", scope="user"),
        _sample_tool("ws_tool", scope="workspace"),
    ]
    section = format_cyt_mcp_source_section(tools)
    assert "<cyt-mcp-ws>" in section
    assert "<cyt-mcp-usr>" in section
    assert section.index("<cyt-mcp-ws>") < section.index("<cyt-mcp-usr>")


def test_format_cyt_mcp_source_section_workspace_wins_on_name_collision() -> None:
    tools = [
        _sample_tool("shared_tool", scope="user"),
        _sample_tool("shared_tool", scope="workspace"),
    ]
    section = format_cyt_mcp_source_section(tools)
    assert "<cyt-mcp-ws>" in section
    assert "<cyt-mcp-usr>" not in section
    assert section.count("name='shared_tool'") == 1


def test_format_cyt_mcp_source_section_empty_tools_emits_static_block() -> None:
    section = format_cyt_mcp_source_section([])
    assert "<cyt-mcp>" in section
    assert "Do not use `get-tool-definitions`" in section
    assert "<cyt-mcp-ws>" not in section
    assert "<cyt-mcp-usr>" not in section


def test_format_cyt_mcp_source_section_pruned_subset_note() -> None:
    section = format_cyt_mcp_source_section([_sample_tool("codebase-memory_query_graph")])
    assert "pre-filtered tool definitions" in section
    assert "Do not use `get-tool-definitions`" in section
    assert "pruning pipeline" in section


def test_format_cyt_mcp_source_section_omits_note_when_pre_exposed() -> None:
    prior = format_cyt_mcp_source_section([_sample_tool("demo_tool")])
    section = format_cyt_mcp_source_section(
        [_sample_tool("other_tool")],
        session_text=prior,
    )
    assert "pre-filtered tool definitions" not in section
    assert "<cyt-mcp-usr>" in section


def test_multi_source_orders_cyt_mcp_first() -> None:
    wrapped = format_multi_source_agent_tools(
        {
            "executor": "<executor>\nx\n</executor>",
            "cyt_mcp": "<cyt-mcp>\ny\n</cyt-mcp>",
        },
    )
    assert wrapped.index("<cyt-mcp>") < wrapped.index("<executor>")


def test_multi_source_agent_tools_intro_is_inner_text() -> None:
    wrapped = format_multi_source_agent_tools(
        {"cyt_mcp": "<cyt-mcp>\ny\n</cyt-mcp>"},
    )
    assert wrapped.startswith("\n<agent-tools>\n")
    assert "description='" not in wrapped.split("\n", 1)[0]
    assert _AGENT_TOOLS_DESCRIPTION_BASE.split(".")[0] in wrapped


def test_multi_source_agent_tools_omits_intro_when_pre_exposed() -> None:
    prior = format_multi_source_agent_tools(
        {"cyt_mcp": "<cyt-mcp>\ny\n</cyt-mcp>"},
    )
    wrapped = format_multi_source_agent_tools(
        {"executor": "<executor>\nx\n</executor>"},
        session_text=prior,
    )
    assert _AGENT_TOOLS_DESCRIPTION_BASE.split(".")[0] not in wrapped
    assert "<executor>" in wrapped
