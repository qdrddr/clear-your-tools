"""Unit tests for cyt-mcp source injection."""

from __future__ import annotations

from cyt.tools.source_inject import format_cyt_mcp_source_section, format_multi_source_agent_tools


def test_format_cyt_mcp_source_section_wraps_tools() -> None:
    tools = [
        {
            "name": "filesystem_read_file",
            "description": "Read a file",
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
            },
        },
    ]
    section = format_cyt_mcp_source_section(tools)
    assert "<cyt-mcp>" in section
    assert "filesystem_read_file" in section
    assert "path" in section


def test_format_cyt_mcp_source_section_empty_tools_emits_static_block() -> None:
    section = format_cyt_mcp_source_section([])
    assert "<cyt-mcp>" in section
    assert "Do not use `cyt-mcp_search`" in section


def test_format_cyt_mcp_source_section_pruned_subset_note() -> None:
    tools = [
        {
            "name": "codebase-memory-mcp_query_graph",
            "description": "Query graph",
            "input_schema": {
                "type": "object",
                "properties": {"project": {"type": "string"}},
            },
        },
    ]
    section = format_cyt_mcp_source_section(tools)
    assert "not a full server catalog" in section
    assert "cyt-mcp_search" in section


def test_multi_source_orders_cyt_mcp_first() -> None:
    wrapped = format_multi_source_agent_tools(
        {
            "executor": "<executor>\nx\n</executor>",
            "cyt_mcp": "<cyt-mcp>\ny\n</cyt-mcp>",
        },
    )
    assert wrapped.index("<cyt-mcp>") < wrapped.index("<executor>")
