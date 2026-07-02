"""Tests for <agent-tools> injection formatting."""

from __future__ import annotations

from cyt.tools.inject import format_agent_tools


def test_format_agent_tools_moves_description_to_xml_attr() -> None:
    tools = [
        {
            "name": "mcp__filesystem__read_file",
            "description": "Read a file from disk",
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
            },
        },
    ]

    text = format_agent_tools(tools)

    assert "<tool name='mcp__filesystem__read_file' description='Read a file from disk'>" in text
    assert "'description':" not in text
    assert "'input_schema':{'type':'object'" in text
    assert "'name':" not in text


def test_format_agent_tools_escapes_multiline_description() -> None:
    tools = [
        {
            "name": "mcp__demo__tool",
            "description": "Line one.\n\nLine two.",
            "input_schema": {"type": "object", "properties": {}},
        },
    ]

    text = format_agent_tools(tools)

    assert "description='Line one.\\n\\nLine two.'" in text


def test_format_agent_tools_escapes_apostrophe_in_description() -> None:
    tools = [
        {
            "name": "mcp__demo__tool",
            "description": "it's fine",
            "input_schema": {"type": "object", "properties": {}},
        },
    ]

    text = format_agent_tools(tools)

    assert "description='it&apos;s fine'" in text
