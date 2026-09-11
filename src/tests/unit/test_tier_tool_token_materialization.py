"""Tests for tier-scoped tool token materialization."""

from __future__ import annotations

from cyt.tiers.tool_token_materialization import (
    carried_tool_token_count,
    clear_carried_token_memo,
    effective_tool_token_count,
    schema_for_tier,
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


def test_carried_tool_token_count_uses_cached_value() -> None:
    clear_carried_token_memo()
    tool = _sample_tool()
    tool["token_count"] = 4242
    assert carried_tool_token_count(tool) == 4242
