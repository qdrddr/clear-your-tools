"""Unit tests for tier-aware tool injection formatting."""

from __future__ import annotations

from cyt.tiers.adapters.tools import prepare_tool_for_tier_pipeline
from cyt.tiers.models import Tier
from cyt.tools.inject import format_agent_tools, format_tool_item, format_tools_grouped_by_tier


def _sample_tool(*, name: str = "demo_tool", with_schema: bool = True) -> dict:
    tool: dict = {
        "name": name,
        "description": "Demo tool for injection formatting tests.",
        "cyt_catalog_source": "cyt_mcp",
    }
    if with_schema:
        tool["input_schema"] = {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
        }
    else:
        tool["input_schema"] = {}
    return tool


def test_format_tool_item_omits_empty_input_schema() -> None:
    item = format_tool_item(_sample_tool(with_schema=False))
    assert "input_schema" not in item
    assert item.count("\n") == 1
    assert item.endswith("</tool>")


def test_format_tool_item_includes_non_empty_input_schema() -> None:
    item = format_tool_item(_sample_tool(with_schema=True))
    assert "'input_schema':" in item
    assert "'query'" in item


def test_t1_prepared_tool_formats_without_schema_block() -> None:
    tool = prepare_tool_for_tier_pipeline(_sample_tool(with_schema=True), Tier.COLD)
    item = format_tool_item(tool)
    assert "input_schema" not in item
    assert "Demo tool for injection formatting tests." in item


def test_t2_prepared_tool_formats_required_properties_only() -> None:
    tool = prepare_tool_for_tier_pipeline(_sample_tool(with_schema=True), Tier.ACTIVE)
    item = format_tool_item(tool)
    assert "'query'" in item
    assert "'limit'" not in item


def test_format_tool_item_omits_tier_attribute() -> None:
    tool = _sample_tool(with_schema=True)
    tool["cyt_injection_tier"] = "t2"
    item = format_tool_item(tool)
    assert "tier='" not in item


def test_format_tools_grouped_by_tier_wraps_tools() -> None:
    t2 = _sample_tool(name="t2_tool", with_schema=True)
    t2["cyt_injection_tier"] = "t2"
    t3 = _sample_tool(name="t3_tool", with_schema=True)
    t3["cyt_injection_tier"] = "t3"
    block = format_tools_grouped_by_tier([t3, t2])
    assert "<tier_t3>" in block
    assert "</tier_t3>" in block
    assert "<tier_t2>" in block
    assert "</tier_t2>" in block
    assert block.index("<tier_t3>") < block.index("<tier_t2>")
    for line in block.splitlines():
        if line.lstrip().startswith("<tool "):
            assert "tier='" not in line
    assert "name='t3_tool'" in block
    assert "name='t2_tool'" in block


def test_format_tools_grouped_by_tier_tx_for_t2_without_required() -> None:
    tool = _sample_tool(with_schema=False)
    tool["name"] = "ctx_doctor"
    tool["cyt_injection_tier"] = "t2"
    block = format_tools_grouped_by_tier([tool])
    assert "<tier_tx>" in block
    assert "</tier_tx>" in block
    assert "tier='" not in block


def test_format_tools_grouped_by_tier_untiered_tools_append_unwrapped() -> None:
    tool = _sample_tool(name="plain_tool", with_schema=True)
    block = format_tools_grouped_by_tier([tool])
    assert "<tier" not in block
    assert "name='plain_tool'" in block


def test_format_tool_item_omits_examples_when_absent() -> None:
    item = format_tool_item(_sample_tool(with_schema=True))
    assert "<examples>" not in item


def test_format_tool_item_omits_examples_when_list_empty() -> None:
    tool = _sample_tool(with_schema=True)
    tool["cyt_injection_examples"] = []
    tool["cyt_injection_examples_max_chars"] = 120
    item = format_tool_item(tool)
    assert "<examples>" not in item
    assert "</examples>" not in item


def test_format_tool_item_dedupes_examples_with_input_schema_only() -> None:
    schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "project": {"type": "string"},
        },
        "required": ["pattern", "project"],
    }
    tool = {
        "name": "codebase-memory_search_code",
        "inputSchema": schema,
        "cyt_injection_examples": [
            {"pattern": "BM25", "project": "clear-your-tools"},
            {"pattern": "BM25", "project": "clear-your-tools"},
            {"pattern": "bm25", "project": "clear-your-tools"},
        ],
    }
    item = format_tool_item(tool)
    assert item.count("- {'pattern':'BM25','project':'clear-your-tools'}") == 1
    assert "- {'pattern':'bm25','project':'clear-your-tools'}" in item


def test_format_tool_item_renders_examples_block() -> None:
    tool = _sample_tool(with_schema=True)
    tool["cyt_injection_examples"] = [
        {"query": "useEffect cleanup pattern", "limit": 5},
    ]
    tool["cyt_injection_examples_max_chars"] = 120
    item = format_tool_item(tool)
    assert "<examples>" in item
    assert "- {'limit':5,'query':'useEffect cleanup pattern'}" in item
    assert "</examples>" in item


def test_format_tool_item_zero_max_chars_disables_example_truncation() -> None:
    long_query = "BM25 ranking score " + ("x" * 120)
    tool = _sample_tool(with_schema=True)
    tool["cyt_injection_examples"] = [{"query": long_query}]
    tool["cyt_injection_examples_max_chars"] = 0
    item = format_tool_item(tool)
    assert long_query in item
    assert "..." not in item


def test_format_agent_tools_mixed_t1_and_t3_tools() -> None:
    t1 = prepare_tool_for_tier_pipeline(
        _sample_tool(name="cold_tool", with_schema=True),
        Tier.COLD,
    )
    t3 = prepare_tool_for_tier_pipeline(
        _sample_tool(name="hot_tool", with_schema=True),
        Tier.HOT,
    )
    block = format_agent_tools([t1, t3])
    assert "<agent-tools" in block
    assert "cold_tool" in block
    assert "hot_tool" in block
    cold_section = block.split("cold_tool", 1)[1].split("hot_tool", 1)[0]
    assert "input_schema" not in cold_section
    assert "'input_schema':" in block.split("hot_tool", 1)[1]
