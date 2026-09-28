"""Fixture-driven regression: Type-1/Type-2 session logs preserve cyt_mcp backend identity."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from cyt.injection.session_log_build import (
    _tool_dict_from_log_entry,
    build_tool_catalog_log_entry,
    build_tool_log_entry,
)
from cyt.tools.schema_propagation_contract import (
    assert_backend_identity_preserved,
    assert_explicit_identity_preferred,
    assert_identity_matches_reference,
    assert_type1_record_shape,
)
from cyt_client.session_capture import _tool_record_for_catalog
from cyt_client.session_pre_tool_exposure import build_type1_tool_entry_from_catalog_record
from tests.support.tool_schema_completeness_fixtures import (
    load_propagation_reference_tool,
    load_session_log_identity_contract,
    load_session_log_writer_scenarios,
    resolve_reference_tool_from_catalogs,
)


def _tool_for_writer(tool_ref: str) -> dict[str, Any]:
    tool = resolve_reference_tool_from_catalogs(tool_ref)
    tool.setdefault("cyt_catalog_source", "cyt_mcp")
    return tool


def _emit_session_log_record(writer: str, tool: dict[str, Any]) -> dict[str, Any]:
    if writer == "hook_type1":
        return build_tool_log_entry(tool, catalog="cyt_mcp", full=False)
    if writer == "hook_type2":
        entry = build_tool_catalog_log_entry("cyt_mcp", [tool])
        return next(item for item in entry["tools"] if item["name"] == tool["name"])
    if writer == "client_type1":
        return build_type1_tool_entry_from_catalog_record(tool, catalog="cyt_mcp", full=True)
    if writer == "client_type2":
        definition = {
            "name": str(tool.get("name") or ""),
            "inputSchema": copy.deepcopy(tool.get("input_schema") or {}),
            "server_key": str(tool.get("server_key") or ""),
            "tool_name": str(tool.get("tool_name") or ""),
        }
        return _tool_record_for_catalog(str(tool.get("name") or ""), definition)
    raise ValueError(f"unknown session log writer {writer!r}")


@pytest.mark.parametrize(
    "row",
    load_session_log_writer_scenarios(),
    ids=lambda row: str(row["id"]),
)
def test_session_log_writers_stamp_backend_identity(row: dict[str, Any]) -> None:
    ref = load_propagation_reference_tool(str(row["tool_ref"]))
    tool = _tool_for_writer(ref.id)
    record = _emit_session_log_record(str(row["writer"]), tool)
    assert_identity_matches_reference(
        record,
        wire_name=ref.id,
        server_key=ref.server_key,
        tool_name=ref.tool_name,
    )


@pytest.mark.parametrize(
    "writer",
    load_session_log_identity_contract()["reject_missing_identity_writers"],
    ids=lambda writer: f"reject-{writer}",
)
def test_session_log_writers_reject_missing_backend_identity(writer: str) -> None:
    block = load_session_log_identity_contract()
    ref = load_propagation_reference_tool(str(block["reject_missing_identity_tool_ref"]))
    tool = _tool_for_writer(ref.id)
    tool.pop("server_key", None)
    tool.pop("tool_name", None)
    with pytest.raises(ValueError, match="missing explicit server_key/tool_name"):
        _emit_session_log_record(writer, tool)


def test_meta_tool_type1_emit_does_not_require_backend_identity() -> None:
    block = load_session_log_identity_contract()
    meta_name = str(block["meta_tool_exempt"])
    entry = build_type1_tool_entry_from_catalog_record(
        {
            "name": meta_name,
            "input_schema": {
                "type": "object",
                "properties": {"tool_name": {"type": "string"}},
                "required": ["tool_name"],
            },
        },
        catalog="cyt_mcp",
        full=True,
    )
    assert entry["name"] == meta_name
    assert "server_key" not in entry
    assert "tool_name" not in entry


def test_type1_round_trip_restores_backend_identity() -> None:
    block = load_session_log_identity_contract()
    ref = load_propagation_reference_tool(str(block["round_trip_tool_ref"]))
    tool = _tool_for_writer(ref.id)
    entry = build_tool_log_entry(tool, catalog="cyt_mcp", full=True)
    assert_type1_record_shape(entry)
    restored = _tool_dict_from_log_entry(entry)
    assert_identity_matches_reference(
        restored,
        wire_name=ref.id,
        server_key=ref.server_key,
        tool_name=ref.tool_name,
    )


def test_client_capture_enriches_wire_only_definition_with_identity() -> None:
    block = load_session_log_identity_contract()
    capture = block["capture_enrichment"]
    wire_name = str(capture["wire_name"])
    record = _tool_record_for_catalog(
        wire_name,
        {
            "name": wire_name,
            "inputSchema": copy.deepcopy(capture["input_schema"]),
        },
    )
    assert record["server_key"] == capture["expected_server_key"]
    assert record["tool_name"] == capture["expected_tool_name"]
    assert_backend_identity_preserved(record)


def test_explicit_first_resolution_prefers_catalog_fields_over_wire_split() -> None:
    block = load_session_log_identity_contract()
    case = block["explicit_first_resolution"]
    tool = {
        "name": case["wire_name"],
        "server_key": case["server_key"],
        "tool_name": case["tool_name"],
    }
    assert_explicit_identity_preferred(
        tool,
        list(case["server_keys"]),
        expected_server=str(case["expected_server_key"]),
        expected_bare=str(case["expected_tool_name"]),
    )
