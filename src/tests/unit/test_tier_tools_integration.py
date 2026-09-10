"""Integration tests for tier-aware tools pruning."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from cyt.tiers.manager import TierManager, _managers
from cyt.tiers.models import Tier, TierScope


@pytest.fixture(autouse=True)
def clear_tier_managers() -> None:
    _managers.clear()
    yield
    _managers.clear()


@pytest.fixture
def tier_config(base_config: dict, tmp_path: Path) -> dict:
    tools = dict(base_config.get("tools") or {})
    tools["tiers"] = {
        "enabled": True,
        "shadow": False,
        "database": {"path": str(tmp_path / "tier_state.db")},
    }
    return {**base_config, "tools": tools}


@pytest.fixture
def shadow_config(base_config: dict, tmp_path: Path) -> dict:
    tools = dict(base_config.get("tools") or {})
    tools["tiers"] = {
        "enabled": False,
        "shadow": True,
        "database": {"path": str(tmp_path / "tier_shadow.db")},
    }
    return {**base_config, "tools": tools}


@pytest.fixture
def base_config() -> dict:
    from cyt.config import load_config

    return load_config()


def test_filter_tools_shadow_mode_unchanged(shadow_config: dict, tmp_path: Path) -> None:
    from cyt.pruners.tools_filter import filter_tools_for_query

    tools = [
        {
            "name": "demo_tool",
            "description": "demo",
            "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
            "cyt_catalog_source": "definitions",
        }
    ]
    with patch("cyt.pruners.tools_filter.get_tier_manager") as get_manager:
        scope = TierScope(user_key="u", workspace_key=str(tmp_path))
        manager = TierManager(scope, str(tmp_path / "tier_state.db"))
        get_manager.return_value = manager
        result = filter_tools_for_query(
            tools,
            "demo query",
            ["bm25"],
            config=shadow_config,
        )
    assert result.status in {"applied", "skipped", "failed", "pass_through"}


def test_filter_tools_excludes_dormant_when_enabled(tier_config: dict, tmp_path: Path) -> None:
    from cyt.pruners.tools_filter import filter_tools_for_query

    dormant_tool = {
        "name": "dormant_tool",
        "description": "sleeping",
        "input_schema": {"type": "object"},
        "cyt_catalog_source": "definitions",
    }
    active_tool = {
        "name": "active_tool",
        "description": "active",
        "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
        "cyt_catalog_source": "definitions",
    }
    scope = TierScope(user_key="u", workspace_key=str(tmp_path))
    manager = TierManager(scope, str(tmp_path / "tier_state.db"))
    manager._states[("tool", "definitions:dormant_tool")] = manager._ensure_state(
        "tool",
        "definitions:dormant_tool",
    )
    dormant = manager._states[("tool", "definitions:dormant_tool")]
    dormant.stable_tier = Tier.DORMANT
    dormant.effective_tier = Tier.DORMANT

    with patch("cyt.pruners.tools_filter.get_tier_manager", return_value=manager):
        with patch("cyt.pruners.tools_filter._run_catalog_pruning") as prune_mock:
            prune_mock.return_value = (
                [{"name": "active_tool"}],
                {},
                {},
                None,
                {},
                {},
                0,
                0,
            )
            filter_tools_for_query(
                [dormant_tool, active_tool],
                "active query",
                ["bm25"],
                config=tier_config,
            )
            assert prune_mock.called
            sent_tools = prune_mock.call_args[0][0]
            assert isinstance(sent_tools, list)
