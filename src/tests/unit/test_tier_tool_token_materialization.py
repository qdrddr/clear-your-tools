"""Tests for tier-scoped tool token materialization."""

from __future__ import annotations

from cyt.tiers.adapters.tools import prepare_tool_for_tier_pipeline
from cyt.tiers.models import Tier
from cyt.tiers.tool_token_materialization import (
    CYT_BACKEND_INPUT_SCHEMA,
    carried_tool_token_count,
    clear_carried_token_memo,
    effective_tool_token_count,
    schema_for_tier,
    stamp_tool_dual_schema,
)


def _sample_tool() -> dict:
    return {
        "name": "search",
        "cyt_catalog_source": "cyt_mcp",
        "description": "Search the codebase for symbols and files.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "search query text"},
                "limit": {"type": "integer", "description": "max results"},
            },
            "required": ["query"],
        },
    }


def test_schema_for_tier_trims_required_only_at_t2() -> None:
    schema = _sample_tool()["input_schema"]
    assert schema_for_tier(schema, "T1") == {}
    t2 = schema_for_tier(schema, "T2")
    assert list(t2.get("properties", {}).keys()) == ["query"]
    assert t2.get("required") == ["query"]
    t4 = schema_for_tier(schema, "T4")
    assert set(t4.get("properties", {}).keys()) == {"query", "limit"}


def test_tool_token_counts_order_by_tier() -> None:
    clear_carried_token_memo()
    tool = _sample_tool()
    carried = carried_tool_token_count(tool)
    eff_t1 = effective_tool_token_count(tool, "T1")
    eff_t2 = effective_tool_token_count(tool, "T2")
    eff_t3 = effective_tool_token_count(tool, "T3")
    eff_t4 = effective_tool_token_count(tool, "T4")
    assert effective_tool_token_count(tool, "T0") == 0
    assert eff_t1 <= eff_t2 <= eff_t3
    assert eff_t3 == eff_t4 == carried
    assert carried > 0


def test_carried_equals_effective_at_t4() -> None:
    clear_carried_token_memo()
    tool = _sample_tool()
    assert carried_tool_token_count(tool) == effective_tool_token_count(tool, "T4")


def test_carried_tool_token_count_ignores_stale_cached_value() -> None:
    clear_carried_token_memo()
    tool = _sample_tool()
    fresh = carried_tool_token_count(tool)
    assert fresh > 0
    clear_carried_token_memo()
    tool["token_count"] = 1
    assert carried_tool_token_count(tool) == fresh


def test_effective_never_exceeds_carried_for_fixture_catalog() -> None:
    clear_carried_token_memo()
    tool = _sample_tool()
    carried = carried_tool_token_count(tool)
    for tier in ("T1", "T2", "T3", "T4"):
        effective = effective_tool_token_count(tool, tier)
        assert effective <= carried
    assert effective_tool_token_count(tool, "T3") == effective_tool_token_count(tool, "T4") == carried


def test_stamp_tool_dual_schema_preserves_backend_and_tier_scope() -> None:
    tool = _sample_tool()
    t2 = stamp_tool_dual_schema(tool, "T2")
    backend = t2[CYT_BACKEND_INPUT_SCHEMA]
    assert set(backend.get("properties", {}).keys()) == {"query", "limit"}
    assert list(t2["input_schema"].get("properties", {}).keys()) == ["query"]
    t3 = stamp_tool_dual_schema(tool, "T3")
    assert set(t3["input_schema"].get("properties", {}).keys()) == {"query", "limit"}
    assert t3[CYT_BACKEND_INPUT_SCHEMA] == backend


def test_prepare_tool_for_tier_pipeline_stamps_t4_backend() -> None:
    tool = _sample_tool()
    t4 = prepare_tool_for_tier_pipeline(tool, Tier.EXTRA_HOT)
    assert set(t4[CYT_BACKEND_INPUT_SCHEMA].get("properties", {}).keys()) == {"query", "limit"}
    assert set(t4["input_schema"].get("properties", {}).keys()) == {"query", "limit"}


def test_prepare_tool_for_tier_pipeline_stamps_t1_backend_with_empty_tier_schema() -> None:
    tool = _sample_tool()
    t1 = prepare_tool_for_tier_pipeline(tool, Tier.COLD)
    assert set(t1[CYT_BACKEND_INPUT_SCHEMA].get("properties", {}).keys()) == {"query", "limit"}
    assert t1["input_schema"] == {}
