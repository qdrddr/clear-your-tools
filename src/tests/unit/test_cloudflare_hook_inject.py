"""Cloudflare hook injection formatting."""

from __future__ import annotations

from cyt.tools.source_inject import (
    format_cloudflare_source_section,
    format_multi_source_agent_tools,
)


def test_format_cloudflare_source_section() -> None:
    section = format_cloudflare_source_section(
        [
            {
                "name": "context7_query-docs",
                "description": "Query docs",
                "input_schema": {"type": "object"},
            },
        ],
    )
    assert section.startswith(
        "<cloudflare>\nListed below are pre-filtered upstream MCP tools and their relevant definitions,",
    )
    assert "Portal URL:" not in section
    assert "code_mode" in section
    assert "Do not use `portal_list_servers` unless" in section
    assert "**all available** MCP servers" in section
    assert "context7_query-docs" in section


def test_format_multi_source_includes_cloudflare() -> None:
    wrapped = format_multi_source_agent_tools(
        {
            "cloudflare": format_cloudflare_source_section(
                [{"name": "context7_query-docs", "input_schema": {}}],
            ),
        },
    )
    assert "<cloudflare>" in wrapped
    assert "<cloudflare>" in wrapped
