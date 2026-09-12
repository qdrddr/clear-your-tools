"""Tests for tri-state tier mode configuration."""

from __future__ import annotations

import pytest

from cyt.tiers.config import TierMode, tier_section_config, tiers_active, tiers_apply


@pytest.mark.parametrize(
    ("tiers_block", "expected_mode"),
    [
        ({"mode": "off"}, TierMode.OFF),
        ({"mode": "shadow"}, TierMode.SHADOW),
        ({"mode": "live"}, TierMode.LIVE),
        ({}, TierMode.SHADOW),
    ],
)
def test_resolve_tier_mode(tiers_block: dict[str, object], expected_mode: TierMode) -> None:
    cfg = {"tools": {"tiers": tiers_block}}
    section = tier_section_config(cfg, kind="tool")
    assert section.mode == expected_mode


@pytest.mark.parametrize(
    ("mode", "active", "apply"),
    [
        (TierMode.OFF, False, False),
        (TierMode.SHADOW, True, False),
        (TierMode.LIVE, True, True),
    ],
)
def test_tier_mode_semantics(mode: TierMode, *, active: bool, apply: bool) -> None:
    cfg = {"tools": {"tiers": {"mode": mode.value}}}
    assert tiers_active(cfg, kind="tool") is active
    assert tiers_apply(cfg, kind="tool") is apply


def test_skills_inherit_tools_tiers_then_override() -> None:
    cfg = {
        "tools": {"tiers": {"mode": "shadow"}},
        "skills": {"tiers": {"mode": "live"}},
    }
    assert tier_section_config(cfg, kind="tool").mode == TierMode.SHADOW
    assert tier_section_config(cfg, kind="skill").mode == TierMode.LIVE
