"""Tests for <agent-tools> injection formatting."""

from __future__ import annotations

from cyt.tools.inject import ensure_agent_tools_starts_on_new_line, format_agent_tools


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

    assert text.startswith("\n<agent-tools description='Pruned MCP tool definitions below")
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


def test_format_agent_tools_omits_tool_description_when_requested() -> None:
    tools = [
        {
            "name": "mcp__context7__resolve-library-id",
            "description": "Resolve a library ID",
            "parameters": {"type": "object", "properties": {"libraryId": {"type": "string"}}},
        },
    ]

    text = format_agent_tools(tools, include_tool_description=False)

    assert "descriptions are in root tools[] stubs" in text
    assert "<tool name='mcp__context7__resolve-library-id'>" in text
    assert "description='Resolve a library ID'" not in text
    assert "'input_schema':{'type':'object'" in text


def test_format_agent_tools_intro_in_description_avoids_pruned_suffix_on_user_text() -> None:
    """Pruned intro lives on the tag attribute, not as plain text after the user query."""
    tools = [
        {
            "name": "mcp__context7__resolve-library-id",
            "description": "Resolve a library ID",
            "input_schema": {"type": "object", "properties": {}},
        },
    ]

    text = format_agent_tools(tools)
    user_query = "using context7 get language of qdrddr/clear-your-tools"
    user_then_inject = (
        f"{user_query}{ensure_agent_tools_starts_on_new_line(text, after=user_query)}"
    )

    assert "description='Pruned MCP tool definitions below" in text
    assert "Pruned MCP tool definitions below" not in user_then_inject.split("<agent-tools", 1)[0]
    assert "clear-your-toolsPruned" not in user_then_inject


def test_ensure_agent_tools_starts_on_new_line() -> None:
    block = "<agent-tools description='demo'>\n</agent-tools>"
    assert ensure_agent_tools_starts_on_new_line(block, after="hello") == "\n" + block
    assert ensure_agent_tools_starts_on_new_line(block, after="hello\n") == "\n" + block
    assert ensure_agent_tools_starts_on_new_line("\n" + block, after="hello\n") == "\n" + block
