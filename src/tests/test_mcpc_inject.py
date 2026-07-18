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
    assert "mcpc CLI app" in text
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
