"""Gherkin steps for session log cyt_mcp backend identity contract."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from cyt.injection.session_log_build import _tool_dict_from_log_entry, build_tool_log_entry
from cyt.tools.schema_propagation_contract import (
    assert_backend_identity_preserved,
    assert_identity_matches_reference,
)
from cyt_client.session_capture import _tool_record_for_catalog
from cyt_client.session_pre_tool_exposure import build_type1_tool_entry_from_catalog_record
from cyt_client.tool_gate import _resolve_mcp_server_and_tool_name
from tests.support.tool_schema_completeness_fixtures import (
    load_propagation_reference_tool,
    load_session_log_identity_contract,
    resolve_reference_tool_from_catalogs,
)
from tests.unit.gherkin.conftest import GherkinContext
from tests.unit.test_session_log_identity_contract import _emit_session_log_record

FEATURES = Path(__file__).resolve().parent / "features" / "session_log_identity.feature"
scenarios(str(FEATURES))

pytestmark = pytest.mark.gherkin


@given("agent cursor")
def given_agent_cursor(gherkin_context: GherkinContext) -> None:
    gherkin_context.agent = "cursor"


@given(parsers.parse("a reference cyt_mcp tool {tool_ref} from propagation contract"))
def given_reference_tool(tool_ref: str, gherkin_context: GherkinContext) -> None:
    ref = load_propagation_reference_tool(tool_ref)
    tool = resolve_reference_tool_from_catalogs(tool_ref)
    tool.setdefault("cyt_catalog_source", "cyt_mcp")
    gherkin_context.payload["reference"] = ref
    gherkin_context.payload["tool"] = tool


@given(
    parsers.parse(
        "a reference cyt_mcp tool {tool_ref} from propagation contract without backend identity",
    ),
)
def given_reference_tool_without_identity(tool_ref: str, gherkin_context: GherkinContext) -> None:
    given_reference_tool(tool_ref, gherkin_context)
    tool = gherkin_context.payload["tool"]
    tool.pop("server_key", None)
    tool.pop("tool_name", None)


@given(
    parsers.parse(
        "a cyt_mcp catalog tool with explicit server_key {server_key} and tool_name {bare_name} "
        "for wire {wire_name}",
    ),
)
def given_explicit_catalog_tool(
    server_key: str,
    bare_name: str,
    wire_name: str,
    gherkin_context: GherkinContext,
) -> None:
    gherkin_context.payload["explicit_tool"] = {
        "name": wire_name,
        "server_key": server_key,
        "tool_name": bare_name,
        "input_schema": {"type": "object", "properties": {"project": {"type": "string"}}},
    }


@when(parsers.parse("session log identity writer {writer} emits a record for the tool"))
def when_writer_emits(writer: str, gherkin_context: GherkinContext) -> None:
    tool = copy.deepcopy(gherkin_context.payload["tool"])
    gherkin_context.payload["writer_error"] = None
    try:
        gherkin_context.payload["emitted_record"] = _emit_session_log_record(writer, tool)
    except ValueError as exc:
        gherkin_context.payload["writer_error"] = exc


@when("hook Type-1 session log entry is built and converted back to tool dict")
def when_type1_round_trip(gherkin_context: GherkinContext) -> None:
    tool = copy.deepcopy(gherkin_context.payload["tool"])
    entry = build_tool_log_entry(tool, catalog="cyt_mcp", full=True)
    gherkin_context.payload["restored_tool"] = _tool_dict_from_log_entry(entry)


@when(
    parsers.parse(
        "client session capture builds Type-2 record for {wire_name}",
    ),
)
def when_client_capture_builds(wire_name: str, gherkin_context: GherkinContext) -> None:
    block = load_session_log_identity_contract()
    capture = block["capture_enrichment"]
    record = _tool_record_for_catalog(
        wire_name,
        {
            "name": wire_name,
            "inputSchema": copy.deepcopy(capture["input_schema"]),
        },
    )
    gherkin_context.payload["emitted_record"] = record


@when("backend identity is resolved for post-tool capture")
def when_resolve_for_capture(gherkin_context: GherkinContext) -> None:
    block = load_session_log_identity_contract()
    case = block["explicit_first_resolution"]
    tool = gherkin_context.payload.get("explicit_tool") or {
        "name": case["wire_name"],
        "server_key": case["server_key"],
        "tool_name": case["tool_name"],
        "input_schema": {"type": "object"},
    }
    server_keys = list(case["server_keys"])
    gherkin_context.payload["resolved_identity"] = _resolve_mcp_server_and_tool_name(
        tool,
        str(tool.get("name") or ""),
        server_keys=server_keys,
    )


@then(parsers.parse("emitted record should preserve reference identity for {tool_ref}"))
def then_emitted_matches_reference(tool_ref: str, gherkin_context: GherkinContext) -> None:
    ref = load_propagation_reference_tool(tool_ref)
    record = gherkin_context.payload["emitted_record"]
    assert_identity_matches_reference(
        record,
        wire_name=ref.id,
        server_key=ref.server_key,
        tool_name=ref.tool_name,
    )


@then("build should fail with missing identity error")
def then_build_fails_missing_identity(gherkin_context: GherkinContext) -> None:
    error = gherkin_context.payload.get("writer_error")
    assert isinstance(error, ValueError)
    assert "missing explicit server_key/tool_name" in str(error)


@then(parsers.parse("restored tool dict should preserve reference identity for {tool_ref}"))
def then_restored_matches_reference(tool_ref: str, gherkin_context: GherkinContext) -> None:
    ref = load_propagation_reference_tool(tool_ref)
    restored = gherkin_context.payload["restored_tool"]
    assert_identity_matches_reference(
        restored,
        wire_name=ref.id,
        server_key=ref.server_key,
        tool_name=ref.tool_name,
    )


@then(
    parsers.parse(
        "Type-2 record {wire_name} should have server_key {server_key} and tool_name {bare_name}",
    ),
)
def then_type2_capture_identity(
    wire_name: str,
    server_key: str,
    bare_name: str,
    gherkin_context: GherkinContext,
) -> None:
    record = gherkin_context.payload["emitted_record"]
    assert record["name"] == wire_name
    assert record["server_key"] == server_key
    assert record["tool_name"] == bare_name
    assert_backend_identity_preserved(record)


@then(
    parsers.parse(
        "resolved identity should be server_key {server_key} and tool_name {bare_name}",
    ),
)
def then_resolved_identity(server_key: str, bare_name: str, gherkin_context: GherkinContext) -> None:
    resolved = gherkin_context.payload["resolved_identity"]
    assert resolved == (server_key, bare_name)
