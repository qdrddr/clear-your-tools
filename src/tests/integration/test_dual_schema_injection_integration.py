"""Integration tests: dual-schema stamping through live prune pipeline."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from cyt.cyt_mcp.catalog import clear_cyt_mcp_catalog_cache
from cyt.pruners.tools_filter import filter_tools_for_query
from cyt.tiers.manager import TierManager, _managers
from cyt.tiers.tool_token_materialization import CYT_BACKEND_INPUT_SCHEMA
from cyt.tools.inject import format_tool_item
from cyt.tools.master_catalog import clear_master_catalog_cache
from tests.support.dual_schema_injection_fixtures import (
    DualSchemaFixturePack,
    IntegrationInjectionExpectation,
    IntegrationScenario,
    live_tier_config,
    load_integration_scenarios,
    materialize_fixture_pack,
    patch_paths,
    seed_tool_tiers,
    write_disk_catalog,
)


@pytest.fixture(autouse=True)
def clear_tier_managers() -> Iterator[None]:
    _managers.clear()
    yield
    _managers.clear()


@pytest.fixture
def fixture_pack(tmp_path: Path) -> DualSchemaFixturePack:
    return materialize_fixture_pack(tmp_path)


@pytest.fixture
def disk_catalog_pack(
    fixture_pack: DualSchemaFixturePack,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[DualSchemaFixturePack]:
    patch_paths(monkeypatch, fixture_pack)
    clear_master_catalog_cache()
    clear_cyt_mcp_catalog_cache()
    write_disk_catalog(fixture_pack)
    yield fixture_pack
    clear_master_catalog_cache()
    clear_cyt_mcp_catalog_cache()


def _manager_for_pack(pack: DualSchemaFixturePack) -> TierManager:
    return TierManager(pack.workspace, str(pack.db_path))


def _tool_by_name(tools: list[dict], name: str) -> dict:
    for tool in tools:
        if str(tool.get("name") or "") == name:
            return tool
    raise KeyError(name)


def _assert_injection_expectation(
    tool: dict[str, Any],
    expectation: IntegrationInjectionExpectation,
) -> None:
    item = format_tool_item(tool)
    if expectation.hint:
        assert "get-tool-definitions" in item, item
        return
    if expectation.explicit_empty_schema:
        assert "'input_schema':" in item, item
        assert "'properties':{}" in item, item
        assert "'type':'object'" in item, item
        assert "get-tool-definitions" not in item
        return
    for key in expectation.required_keys:
        assert f"'{key}'" in item, item
    for key in expectation.optional_keys:
        assert f"'{key}'" in item, item
    assert "get-tool-definitions" not in item


@pytest.mark.parametrize(
    "scenario",
    load_integration_scenarios(),
    ids=[item.id for item in load_integration_scenarios()],
)
def test_filter_tools_for_query_dual_schema_integration(
    disk_catalog_pack: DualSchemaFixturePack,
    scenario: IntegrationScenario,
) -> None:
    pack = disk_catalog_pack
    seed_tool_tiers(pack, scenario.tool_tiers)
    config = live_tier_config(pack)
    manager = _manager_for_pack(pack)
    try:
        with patch("cyt.pruners.tools_filter.get_tier_manager", return_value=manager):
            result = filter_tools_for_query(
                pack.tools,
                scenario.query,
                ["bm25"],
                config=config,
                for_hook=True,
            )
    finally:
        manager.close()

    assert result.tools is not None
    by_name = {str(tool.get("name") or ""): tool for tool in result.tools}

    for tool_name in scenario.must_include_tools:
        assert tool_name in by_name, sorted(by_name)

    stamped = {name: str(tool.get("cyt_injection_tier") or "") for name, tool in by_name.items()}
    for tool_name, expected_tier in scenario.expected_stamped_tiers.items():
        assert stamped.get(tool_name) == expected_tier

    for tool_name in scenario.expected_has_backend_schema:
        backend = by_name[tool_name].get(CYT_BACKEND_INPUT_SCHEMA)
        assert isinstance(backend, dict), tool_name

    for tool_name, expectation in scenario.expected_injection.items():
        _assert_injection_expectation(by_name[tool_name], expectation)
