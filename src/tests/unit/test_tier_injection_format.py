"""Parametrized unit tests for tier-aware injection formatting."""

from __future__ import annotations

import pytest

from cyt.injection.pre_exposed import filter_pre_exposed_tools
from cyt.injection.tier_legend import TOOL_TIER_LEGEND
from cyt.skills.inject import format_agent_skills_empty
from cyt.tools.inject import format_tool_item
from cyt.tools.mcpc_inject import _format_mcpc_tool_item
from cyt.tools.source_inject import format_cyt_mcp_source_section
from tests.support.tier_injection_fixtures import (
    PreExposureCase,
    load_pre_exposure_cases,
    load_sample_tools,
    sample_tool_by_name,
)


@pytest.mark.parametrize("tool", load_sample_tools(), ids=lambda tool: str(tool.get("name")))
def test_sample_tools_emit_tier_attribute_in_xml(tool: dict) -> None:
    item = format_tool_item(tool)
    tier = str(tool.get("cyt_injection_tier") or "")
    assert f"tier='{tier}'" in item


def test_mcpc_tool_item_emits_tier_attribute() -> None:
    tool = {
        "name": "@demo/demo_tool",
        "tool_name": "demo_tool",
        "mcpc_session": "@demo",
        "description": "Demo MCPC tool",
        "cyt_injection_tier": "t2",
        "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
    }
    item = _format_mcpc_tool_item(tool)
    assert "tier='t2'" in item


@pytest.mark.parametrize("case", load_pre_exposure_cases(), ids=lambda case: case.id)
def test_fixture_pre_exposure_cases_skip_when_fragment_present(case: PreExposureCase) -> None:
    tool = sample_tool_by_name(case.tool_ref)
    fragment = format_tool_item(tool)
    filtered = filter_pre_exposed_tools([tool], fragment)
    if case.expects_skipped:
        assert filtered == []
    else:
        assert filtered == [tool]


def test_cyt_mcp_empty_block_includes_tool_legend_from_fixture() -> None:
    section = format_cyt_mcp_source_section([])
    assert TOOL_TIER_LEGEND in section
    assert "No relevant cyt-mcp tools matched" in section


def test_format_agent_skills_empty_matches_fixture_skill_legend() -> None:
    from cyt.injection.tier_legend import SKILL_TIER_LEGEND

    block = format_agent_skills_empty()
    assert SKILL_TIER_LEGEND in block
    assert "<agent-skills>" in block
