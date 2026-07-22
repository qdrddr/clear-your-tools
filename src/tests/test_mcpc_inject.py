"""Tests for MCPC hook injection formatting."""

from __future__ import annotations

from cyt.tools.mcpc_inject import _cli_payload_from_input_schema, format_mcpc_agent_tools

_QUESTIONS_INPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "questions": {
            "description": "Questions to ask the user (1-4 questions)",
            "minItems": 1,
            "maxItems": 4,
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "header": {"type": "string"},
                    "options": {
                        "minItems": 2,
                        "maxItems": 4,
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "description": {"type": "string"},
                            },
                            "required": ["label", "description"],
                            "additionalProperties": False,
                        },
                    },
                    "multiSelect": {"default": False, "type": "boolean"},
                },
                "required": ["question", "header", "options", "multiSelect"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["questions"],
    "additionalProperties": False,
}


def test_cli_payload_from_input_schema_flat_object() -> None:
    payload = _cli_payload_from_input_schema(
        {
            "type": "object",
            "properties": {
                "libraryName": {"type": "string"},
                "query": {"type": "string"},
            },
            "required": ["libraryName", "query"],
        },
    )
    assert payload == {"libraryName": "string", "query": "string"}


def test_cli_payload_from_input_schema_nested_arrays_and_defaults() -> None:
    payload = _cli_payload_from_input_schema(_QUESTIONS_INPUT_SCHEMA)
    assert payload == {
        "questions": [
            {
                "question": "string",
                "header": "string",
                "options": [
                    {"label": "string", "description": "string"},
                    {"label": "string", "description": "string"},
                ],
                "multiSelect": False,
            },
        ],
    }


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
                "required": ["libraryName", "query"],
                "$schema": "http://json-schema.org/draft-07/schema#",
            },
            "annotations": {
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": True,
            },
            "execution": {"taskSupport": "forbidden"},
            "server_name": "Context7",
            "server_instructions": "Use this server for docs.",
        },
    ]
    text = format_mcpc_agent_tools(tools, workspace_paths=["/workspace/repo"])
    assert text.startswith("\n<agent-tools description='Pruned MCP tool definitions below")
    assert "MCPC CLI" in text
    assert "mcpc @session tools-call" in text
    assert "schema.." not in text
    assert "workspace_roots/path" in text
    assert "<server name='Context7' instructions='Use this server for docs.'" in text
    assert "name='resolve-library-id'" in text
    assert "mcpc-session='@ctx7'" in text
    assert "<cli>" in text
    assert "mcpc @ctx7 tools-call resolve-library-id" in text
    assert (
        'echo \'{"libraryName":"string","query":"string"}\' | mcpc @ctx7 tools-call resolve-library-id'
        in text
    )
    assert "<json-schema>" in text
    assert "'type':'object'" in text
    assert "'annotations':{'readOnlyHint':true" in text
    assert "'execution':{'taskSupport':'forbidden'}" in text
    assert "'input_schema':" not in text
    assert "path='/workspace/repo'" in text


def test_format_mcpc_cli_shell_quotes_json_with_single_quotes() -> None:
    tools = [
        {
            "name": "@ctx7/resolve-library-id",
            "tool_name": "resolve-library-id",
            "mcpc_session": "@ctx7",
            "title": "Resolve Context7 Library ID",
            "input_schema": {
                "type": "object",
                "properties": {
                    "libraryName": {"type": "string", "default": "O'Brien"},
                },
                "required": ["libraryName"],
            },
            "server_name": "Context7",
        },
    ]
    text = format_mcpc_agent_tools(tools)
    assert (
        "echo '{\"libraryName\":\"O'\\''Brien\"}' | mcpc @ctx7 tools-call resolve-library-id"
        in text
    )


def test_format_mcpc_agent_tools_nested_cli_payload() -> None:
    tools = [
        {
            "name": "@cursor/AskUserQuestion",
            "tool_name": "AskUserQuestion",
            "mcpc_session": "@cursor",
            "title": "Ask User Question",
            "description": "Ask the user questions",
            "input_schema": _QUESTIONS_INPUT_SCHEMA,
            "server_name": "cursor",
            "server_instructions": "Use for clarifying questions.",
        },
    ]
    text = format_mcpc_agent_tools(tools, workspace_paths=["/workspace/repo"])
    expected_cli = (
        'echo \'{"questions":[{"question":"string","header":"string","options":'
        '[{"label":"string","description":"string"},{"label":"string","description":"string"}],'
        '"multiSelect":false}]}\' | mcpc @cursor tools-call AskUserQuestion'
    )
    assert expected_cli in text


def test_format_mcpc_cli_and_json_schema_match_pruned_properties_only() -> None:
    tools = [
        {
            "name": "@filesystem/read_text_file",
            "tool_name": "read_text_file",
            "mcpc_session": "@filesystem",
            "title": "Read Text File",
            "description": "Read a text file",
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "$schema": "http://json-schema.org/draft-07/schema#",
            },
            "annotations": {"openWorldHint": False, "readOnlyHint": True},
            "execution": {"taskSupport": "forbidden"},
            "server_name": "filesystem",
        },
    ]
    text = format_mcpc_agent_tools(tools)
    assert 'echo \'{"path":"string"}\' | mcpc @filesystem tools-call read_text_file' in text
    assert "'properties':{'path':" in text
    assert "'head':" not in text
    assert "'tail':" not in text


def test_format_mcpc_cli_includes_optional_survivors_in_json_schema() -> None:
    tools = [
        {
            "name": "@filesystem/read_text_file",
            "tool_name": "read_text_file",
            "mcpc_session": "@filesystem",
            "title": "Read Text File",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "head": {
                        "type": "number",
                        "description": "If provided, returns only the first N lines",
                    },
                    "tail": {
                        "type": "number",
                        "description": "If provided, returns only the last N lines",
                    },
                },
                "required": ["path"],
            },
            "server_name": "filesystem",
        },
    ]
    text = format_mcpc_agent_tools(tools)
    assert 'echo \'{"path":"string","head":0,"tail":0}\'' in text
    assert "'head':" in text
    assert "'tail':" in text
