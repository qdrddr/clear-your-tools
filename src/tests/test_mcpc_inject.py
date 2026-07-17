"""Tests for MCPC hook injection formatting."""

from __future__ import annotations

from cyt.tools.mcpc_inject import format_mcpc_agent_tools


def test_format_mcpc_agent_tools_groups_by_server() -> None:
    tools = [
        {
            "name": "@ctx7/resolve-library-id",
            "tool_name": "resolve-library-id",
            "mcpc_session": "@ctx7",
            "title": "Resolve Context7 Library ID",
            "description": "Resolve a library id",
            "input_schema": {
                "type": "object",
                "properties": {
                    "libraryName": {"type": "string"},
                    "query": {"type": "string"},
                },
            },
            "server_name": "Context7",
            "server_instructions": "Use this server for docs.",
        },
    ]
    text = format_mcpc_agent_tools(tools, workspace_paths=["/workspace/repo"])
    assert text.startswith("\n<agent-tools description='Pruned MCP tool definitions below")
    assert "mcpc CLI app" in text
    assert "<server name='Context7' instructions='Use this server for docs.'" in text
    assert "mcpc-session='@ctx7'" in text
    assert "<cli>" in text
    assert "mcpc @ctx7 tools-call resolve-library-id" in text
    assert "<json-schema>" in text
    assert "'input_schema':{'type':'object'" in text
    assert "path='/workspace/repo'" in text
