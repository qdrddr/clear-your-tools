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
        portal_url="https://mcp.example.com",
    )
    assert section.startswith(
        "<cloudflare>\nThese tools are invoked via the Cloudflare MCP portal.",
    )
    assert "Portal URL: https://mcp.example.com/mcp" in section
    assert section.index("Portal URL:") < section.index("<tool")
    assert "context7_query-docs" in section
    assert "CF-Access-Client-Id" in section


def test_format_multi_source_includes_cloudflare() -> None:
    wrapped = format_multi_source_agent_tools(
        {
            "cloudflare": format_cloudflare_source_section(
                [{"name": "context7_query-docs", "input_schema": {}}],
                portal_url="https://mcp.example.com",
            ),
        },
    )
    assert "<cloudflare>" in wrapped
    assert "<cloudflare>" in wrapped
