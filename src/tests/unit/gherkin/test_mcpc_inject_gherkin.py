"""Gherkin steps for MCPC hook injection (wired to test_mcpc_inject)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from cyt.tools.mcpc_inject import _cli_payload_from_input_schema, format_mcpc_agent_tools
from tests.unit.gherkin.conftest import GherkinContext

FEATURES = Path(__file__).resolve().parent / "features" / "mcpc_inject.feature"
scenarios(str(FEATURES))

pytestmark = pytest.mark.gherkin


@given("a flat object input schema with libraryName and query")
def given_flat_schema(gherkin_context: GherkinContext) -> None:
    gherkin_context.payload = {
        "schema": {
            "type": "object",
            "properties": {
                "libraryName": {"type": "string"},
                "query": {"type": "string"},
            },
            "required": ["libraryName", "query"],
        },
    }


@when("CLI payload is built from the input schema")
def when_build_cli_payload(gherkin_context: GherkinContext) -> None:
    gherkin_context.payload["cli_payload"] = _cli_payload_from_input_schema(
        gherkin_context.payload["schema"],
    )


@then("CLI payload should equal flat string placeholders")
def then_flat_payload(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload["cli_payload"] == {
        "libraryName": "string",
        "query": "string",
    }


@given("a Context7 resolve-library-id MCP tool definition")
def given_ctx7_tool(gherkin_context: GherkinContext) -> None:
    gherkin_context.payload = {
        "tools": [
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
        ],
    }


@when(parsers.parse("MCPC agent-tools text is formatted for workspace {path}"))
def when_format_mcpc(gherkin_context: GherkinContext, path: str) -> None:
    gherkin_context.stdout = format_mcpc_agent_tools(
        gherkin_context.payload["tools"],
        workspace_paths=[path],
    )


@then("formatted text should include MCPC server and CLI sections")
def then_mcpc_sections(gherkin_context: GherkinContext) -> None:
    text = gherkin_context.stdout
    assert text.startswith("\n<agent-tools description='Pruned MCP tool definitions below")
    assert "<mcpc>" in text
    assert "MCPC CLI" in text
    assert "mcpc @session tools-call" in text
    assert "<server name='Context7' instructions='Use this server for docs.'" in text
    assert "name='resolve-library-id'" in text
    assert "mcpc-session='@ctx7'" in text
    assert "<cli>" in text
    assert "path='/workspace/repo'" in text


@then("formatted text should include resolve-library-id CLI example")
def then_cli_example(gherkin_context: GherkinContext) -> None:
    text = gherkin_context.stdout
    assert "mcpc @ctx7 tools-call resolve-library-id" in text
    assert (
        'echo \'{"libraryName":"string","query":"string"}\' | mcpc @ctx7 tools-call resolve-library-id'
        in text
    )
