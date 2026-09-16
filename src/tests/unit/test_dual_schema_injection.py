"""Fixture-driven unit tests for dual-schema tier injection."""

from __future__ import annotations

import pytest

from cyt.tiers.adapters.tools import prepare_tool_for_tier_pipeline
from cyt.tiers.tool_token_materialization import CYT_BACKEND_INPUT_SCHEMA
from cyt.tools.inject import format_tool_item
from cyt.tools.injection_schema import (
    ensure_tool_injection_schema,
    injection_needs_definitions_lookup,
)
from tests.support.dual_schema_injection_fixtures import (
    EnsureMergeCase,
    HintCase,
    StampCase,
    build_ensure_merge_tool,
    build_hint_tool,
    load_ensure_merge_cases,
    load_hint_cases,
    load_injection_table_cases,
    load_stamp_cases,
    load_tools_catalog,
    tool_by_name,
)


def test_dual_schema_fixture_sections_are_self_consistent() -> None:
    catalog = load_tools_catalog()
    assert len(catalog) >= 5
    assert len(load_stamp_cases()) >= 5
    assert len(load_hint_cases()) >= 8
    assert len(load_ensure_merge_cases()) >= 2
    assert len(load_injection_table_cases()) == 33
    for case in load_stamp_cases():
        tool_by_name(case.tool_ref, catalog)


@pytest.mark.parametrize("case", load_stamp_cases(), ids=lambda case: case.id)
def test_prepare_tool_stamps_backend_and_tier_scoped_schema(case: StampCase) -> None:
    tool = tool_by_name(case.tool_ref)
    prepared = prepare_tool_for_tier_pipeline(tool, case.tier)
    backend = prepared.get(CYT_BACKEND_INPUT_SCHEMA) or {}
    backend_props_raw = backend.get("properties") if isinstance(backend, dict) else None
    backend_props = backend_props_raw if isinstance(backend_props_raw, dict) else {}
    tier_schema = prepared.get("input_schema") or {}
    tier_props_raw = tier_schema.get("properties") if isinstance(tier_schema, dict) else None
    tier_props = tier_props_raw if isinstance(tier_props_raw, dict) else {}

    assert list(backend_props.keys()) == list(case.backend_property_keys)
    assert list(tier_props.keys()) == list(case.tier_scoped_property_keys)


@pytest.mark.parametrize("case", load_hint_cases(), ids=lambda case: case.id)
def test_injection_needs_definitions_lookup_matches_fixture(case: HintCase) -> None:
    tool = build_hint_tool(case)
    assert injection_needs_definitions_lookup(tool) is case.expects_hint


@pytest.mark.parametrize("case", load_hint_cases(), ids=lambda case: case.id)
def test_format_tool_item_matches_fixture_hint_expectations(case: HintCase) -> None:
    tool = build_hint_tool(case)
    if case.expects_required_keys:
        tool = ensure_tool_injection_schema(tool)
    item = format_tool_item(tool)

    if case.expects_hint:
        assert "get-tool-definitions" in item
        assert "'input_schema':" not in item
    elif case.expects_explicit_empty_schema:
        assert "'input_schema':" in item
        assert "'properties':{}" in item
        assert "'type':'object'" in item
        assert "get-tool-definitions" not in item
    else:
        assert "get-tool-definitions" not in item
        for key in case.injected_property_keys:
            assert f"'{key}'" in item
        for key in case.expects_required_keys:
            assert f"'{key}'" in item


@pytest.mark.parametrize("case", load_ensure_merge_cases(), ids=lambda case: case.id)
def test_ensure_tool_injection_schema_restores_required_from_backend(
    case: EnsureMergeCase,
) -> None:
    tool = build_ensure_merge_tool(case)
    merged = ensure_tool_injection_schema(tool)
    props = merged["input_schema"].get("properties", {})
    assert list(props.keys()) == list(case.expected_property_keys)


def test_format_tool_item_never_emits_internal_backend_key() -> None:
    tool = build_hint_tool(
        HintCase(
            id="internal_key_check",
            tool_ref="dual_tool_required_optional",
            tier="t3",
            injected_property_keys=("query", "limit"),
            expects_hint=False,
            expects_explicit_empty_schema=False,
            expects_required_keys=(),
        ),
    )
    item = format_tool_item(tool)
    assert CYT_BACKEND_INPUT_SCHEMA not in item
