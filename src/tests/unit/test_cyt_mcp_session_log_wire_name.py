"""Fixture-driven regression tests for cyt_mcp session log wire names."""

from __future__ import annotations

from typing import Any

import pytest

from cyt.injection.pre_exposure_context import PreExposureContext
from cyt.injection.pre_exposure_pipeline import gate_and_filter_tools
from cyt.injection.session_log_build import (
    build_tool_log_entry,
    format_entry_fragment,
    format_tool_fragment,
    tool_content_hash,
    tool_item_key,
)
from cyt.tools.inject import format_tool_item
from tests.support.cyt_mcp_session_log_wire_name_fixtures import (
    GateSkipExpectation,
    ToolLogWriteExpectation,
    WireNameFixturePack,
    catalog_tool_by_wire_name,
    catalog_tools_list,
    load_fixture_pack,
    load_gate_skip_expectations,
    load_tool_log_write_expectations,
    session_entry_for_source,
)


@pytest.fixture(scope="module")
def wire_name_fixture_pack() -> WireNameFixturePack:
    return load_fixture_pack()


@pytest.fixture(scope="module")
def catalog_tools() -> list[dict[str, Any]]:
    return catalog_tools_list()


@pytest.mark.parametrize(
    "expectation",
    load_tool_log_write_expectations(),
    ids=lambda item: item.id,
)
def test_build_tool_log_entry_uses_wire_name_from_fixture(
    expectation: ToolLogWriteExpectation,
    catalog_tools: list[dict[str, Any]],
) -> None:
    tool = catalog_tool_by_wire_name(expectation.wire_name)
    entry = build_tool_log_entry(
        tool,
        catalog="cyt_mcp",
        full=False,
        catalog_tools=catalog_tools,
    )
    assert entry["key"] == expectation.expected_key
    assert entry["name"] == expectation.wire_name
    assert tool_item_key(tool, catalog="cyt_mcp") == expectation.expected_key

    injection_xml = format_tool_item(tool)
    session_xml = format_entry_fragment(entry)
    assert f"name='{expectation.expected_xml_name_attr}'" in injection_xml
    assert f"name='{expectation.expected_xml_name_attr}'" in session_xml
    assert f"name='{expectation.must_not_xml_name_attr}'" not in session_xml


@pytest.mark.parametrize(
    "expectation",
    load_tool_log_write_expectations(),
    ids=lambda item: f"hash-{item.id}",
)
def test_tool_content_hash_uses_wire_name_for_cyt_mcp(
    expectation: ToolLogWriteExpectation,
    catalog_tools: list[dict[str, Any]],
) -> None:
    tool = catalog_tool_by_wire_name(expectation.wire_name)
    entry = build_tool_log_entry(
        tool,
        catalog="cyt_mcp",
        full=True,
        catalog_tools=catalog_tools,
    )
    direct_hash = tool_content_hash(tool, catalog="cyt_mcp", catalog_tools=catalog_tools)
    assert entry["hash"] == direct_hash


def test_injection_and_session_log_fragments_match_for_fff_grep(
    catalog_tools: list[dict[str, Any]],
) -> None:
    tool = catalog_tool_by_wire_name("fff_grep")
    entry = build_tool_log_entry(
        tool,
        catalog="cyt_mcp",
        full=False,
        catalog_tools=catalog_tools,
    )
    assert format_tool_fragment(tool, catalog="cyt_mcp", full=False) == format_entry_fragment(
        entry,
    )


@pytest.mark.parametrize(
    "expectation",
    load_gate_skip_expectations(),
    ids=lambda item: item.id,
)
def test_gate_and_filter_tools_respects_wire_vs_legacy_session_entries(
    expectation: GateSkipExpectation,
    catalog_tools: list[dict[str, Any]],
) -> None:
    tool = catalog_tool_by_wire_name(expectation.wire_name)
    session_entry = session_entry_for_source(
        expectation.session_entry_source,
        wire_name=expectation.wire_name,
        catalog_tools=catalog_tools,
    )
    ctx = PreExposureContext.from_entries(payload_text="", entries=[session_entry])
    gated, log_entries, _ = gate_and_filter_tools(
        [tool],
        config={},
        ctx=ctx,
        catalog_tools=catalog_tools,
        source_id="cyt_mcp",
    )
    if expectation.expect_skipped:
        assert gated == []
        assert log_entries == []
    else:
        assert len(gated) == 1
        assert gated[0]["name"] == expectation.wire_name
        assert len(log_entries) == 1
        assert log_entries[0]["name"] == expectation.wire_name


def test_legacy_bare_session_entry_does_not_share_key_with_wire_tool(
    catalog_tools: list[dict[str, Any]],
) -> None:
    tool = catalog_tool_by_wire_name("fff_grep")
    wire_key = tool_item_key(tool, catalog="cyt_mcp")
    legacy = session_entry_for_source("legacy_bare_grep", wire_name="fff_grep")
    assert legacy["key"] == "tool:cyt_mcp:grep"
    assert legacy["name"] == "grep"
    assert wire_key == "tool:cyt_mcp:fff_grep"
    assert legacy["key"] != wire_key
