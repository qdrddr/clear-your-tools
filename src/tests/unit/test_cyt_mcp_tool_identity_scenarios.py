"""Fixture-driven regression tests for cyt-mcp wire name identity mapping."""

from __future__ import annotations

from pathlib import Path

import pytest

from cyt.injection.session_log_build import build_tool_catalog_log_entry
from cyt_client.tool_gate import validate_pre_tool_call
from cyt_mcp.tool_identity import enrich_tool_identity, split_wire_name
from tests.support.cyt_mcp_tool_identity_fixtures import (
    EnrichmentExpectation,
    GateScenario,
    SessionLogRejection,
    SessionLogRequirement,
    ToolIdentityFixturePack,
    catalog_for_variant,
    enrich_backend_tools,
    load_backend_tools,
    load_enrichment_expectations,
    load_fixture_pack,
    load_gate_scenarios,
    load_server_keys,
    load_session_log_rejections,
    load_session_log_requirements,
    patch_session_log_resolver,
    write_type2_session_log,
)


@pytest.fixture(scope="module")
def fixture_pack() -> ToolIdentityFixturePack:
    return load_fixture_pack()


@pytest.mark.parametrize(
    "expectation",
    load_enrichment_expectations(),
    ids=lambda item: item.wire_name,
)
def test_enrich_tool_identity_matches_fixture_expectations(
    expectation: EnrichmentExpectation,
) -> None:
    server_keys = load_server_keys()
    tool = {"name": expectation.wire_name}
    enriched = enrich_tool_identity(tool, list(server_keys))
    assert enriched["server_key"] == expectation.server_key
    assert enriched["tool_name"] == expectation.tool_name
    assert enriched["name"] == expectation.wire_name


def test_enrich_backend_tools_fixture_assigns_identity_to_all_tools() -> None:
    pack = load_fixture_pack()
    for tool in pack.enriched_tools:
        assert str(tool.get("server_key") or "").strip()
        assert str(tool.get("tool_name") or "").strip()
        assert str(tool.get("name") or "").strip()


@pytest.mark.parametrize(
    "expectation",
    load_enrichment_expectations(),
    ids=lambda item: f"split-{item.wire_name}",
)
def test_split_wire_name_longest_prefix_from_fixture(
    expectation: EnrichmentExpectation,
) -> None:
    server_keys = load_server_keys()
    identity = split_wire_name(expectation.wire_name, list(server_keys))
    assert identity is not None
    assert identity.server_key == expectation.server_key
    assert identity.backend_tool_name == expectation.tool_name


@pytest.mark.parametrize(
    "requirement",
    load_session_log_requirements(),
    ids=lambda item: item.expected_record_name,
)
def test_build_tool_catalog_preserves_wire_names_from_fixture(
    requirement: SessionLogRequirement,
) -> None:
    entry = build_tool_catalog_log_entry("cyt_mcp", [requirement.tool])
    assert len(entry["tools"]) == 1
    record = entry["tools"][0]
    assert record["name"] == requirement.expected_record_name
    assert record.get("server_key")
    assert record.get("tool_name")


@pytest.mark.parametrize(
    "rejection",
    load_session_log_rejections(),
    ids=lambda item: item.id,
)
def test_build_tool_catalog_rejects_missing_identity_from_fixture(
    rejection: SessionLogRejection,
) -> None:
    with pytest.raises(ValueError, match=rejection.error_contains):
        build_tool_catalog_log_entry("cyt_mcp", [rejection.tool])


@pytest.mark.parametrize(
    "scenario",
    load_gate_scenarios(),
    ids=lambda item: item.id,
)
def test_gate_scenarios_from_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: GateScenario,
) -> None:
    catalog_tools = catalog_for_variant(
        scenario.catalog,
        subset_wire_names=scenario.catalog_tools,
    )
    log_path = tmp_path / f"{scenario.id}.jsonl"
    write_type2_session_log(
        log_path,
        catalog_tools,
        legacy_bare_catalog=scenario.catalog == "bare_regression",
    )
    patch_session_log_resolver(monkeypatch, log_path)

    validation = validate_pre_tool_call(
        {
            "hook_event_name": "preToolUse",
            "session_id": "session",
            "tool_name": scenario.tool_name,
            "tool_input": scenario.tool_input,
            "workspace_roots": ["/tmp/clear-your-tools"],
        },
    )
    assert validation.allowed is scenario.allowed, (
        f"scenario {scenario.id!r} ({scenario.issue or 'no issue note'}): "
        f"expected allowed={scenario.allowed}, got reason={validation.reason!r}"
    )
    for fragment in scenario.reason_contains:
        assert fragment in validation.reason, (
            f"scenario {scenario.id!r}: expected {fragment!r} in reason, got {validation.reason!r}"
        )
    for fragment in scenario.reason_must_not_contain:
        assert fragment not in validation.reason, (
            f"scenario {scenario.id!r}: regression signature {fragment!r} must not appear, "
            f"got {validation.reason!r}"
        )


def test_wire_catalog_has_distinct_entries_for_shared_bare_tool_name() -> None:
    catalog = catalog_for_variant("wire")
    by_name = {str(tool["name"]): tool for tool in catalog}
    graphify = by_name["graphify_query_graph"]
    codebase = by_name["codebase-memory_query_graph"]
    assert graphify["tool_name"] == "query_graph"
    assert codebase["tool_name"] == "query_graph"
    assert graphify["server_key"] != codebase["server_key"]
    assert graphify["input_schema"] != codebase["input_schema"]


def test_bare_regression_catalog_does_not_include_wire_names() -> None:
    catalog = catalog_for_variant("bare_regression")
    names = {str(tool["name"]) for tool in catalog}
    assert "graphify_query_graph" not in names
    assert "codebase-memory_query_graph" not in names
    assert "query_graph" in names


def test_backend_tools_fixture_includes_collision_pair() -> None:
    tools = load_backend_tools()
    names = {str(tool["name"]) for tool in tools}
    assert "graphify_query_graph" in names
    assert "codebase-memory_query_graph" in names
    enriched = enrich_backend_tools(tools, load_server_keys())
    bare_names = [
        str(tool["tool_name"]) for tool in enriched if tool.get("tool_name") == "query_graph"
    ]
    assert len(bare_names) == 2
