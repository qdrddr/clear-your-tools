"""Unit tests for tier legend helpers."""

from __future__ import annotations

import pytest

from cyt.injection.header_pre_exposed import (
    skill_tier_legend_pre_exposed,
    tool_tier_legend_pre_exposed,
)
from cyt.injection.tier_legend import (
    SKILL_TIER_LEGEND,
    TOOL_TIER_LEGEND,
    injection_tier_attr,
)
from cyt.tiers.models import Tier
from tests.support.tier_injection_fixtures import load_legend_snippets, load_tier_attr_cases


@pytest.mark.parametrize("case", load_tier_attr_cases(), ids=lambda case: case.tier)
def test_injection_tier_attr_normalizes_enum_and_strings(case) -> None:
    tier_enum = Tier[case.tier]
    assert injection_tier_attr(tier_enum) == case.expected_attr
    assert injection_tier_attr(case.expected_attr) == case.expected_attr
    assert injection_tier_attr(case.expected_attr.upper()) == case.expected_attr


def test_injection_tier_attr_returns_none_for_empty() -> None:
    assert injection_tier_attr(None) is None
    assert injection_tier_attr("") is None
    assert injection_tier_attr("   ") is None


@pytest.mark.parametrize(
    ("kind", "legend"),
    [("tool", TOOL_TIER_LEGEND), ("skill", SKILL_TIER_LEGEND)],
)
def test_legend_text_contains_fixture_snippets(kind: str, legend: str) -> None:
    snippets = load_legend_snippets()[kind]
    for snippet in snippets:
        assert snippet in legend


def test_tool_tier_legend_pre_exposed_inside_cyt_mcp_block() -> None:
    prior = f"<cyt-mcp>\n{TOOL_TIER_LEGEND}\n</cyt-mcp>"
    assert tool_tier_legend_pre_exposed(prior, TOOL_TIER_LEGEND) is True
    assert tool_tier_legend_pre_exposed("", TOOL_TIER_LEGEND) is False


def test_skill_tier_legend_pre_exposed_inside_agent_skills_block() -> None:
    prior = f"<agent-skills>\n{SKILL_TIER_LEGEND}\n</agent-skills>"
    assert skill_tier_legend_pre_exposed(prior, SKILL_TIER_LEGEND) is True
    assert skill_tier_legend_pre_exposed("", SKILL_TIER_LEGEND) is False
