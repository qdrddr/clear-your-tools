"""Unit tests: cyt-mcp meta tools must not enter hook catalog or tier tracking."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from cyt.cyt_mcp.catalog import _normalize_tools_list
from cyt.hook.catalog_registry import catalog_for_hook, clear_catalog_registry
from cyt.tiers.adapters.tools import filter_tools_for_tier_tracking, tool_entity_id
from cyt.tiers.feedback import record_tool_attempt_feedback
from cyt.tiers.manager import _managers
from cyt_mcp.catalog_build import hydrate_runtime_cache
from cyt_mcp.config import sample_aggregator_config
from cyt_mcp.runtime_cache import RuntimeToolCache
from cyt_mcp.search import is_meta_tool
from tests.support.tier_capture_fixtures import (
    CatalogLayerRegistrationScenario,
    MetaToolTierFeedbackScenario,
    TierCaptureFixturePack,
    load_catalog_layer_registration_scenarios,
    load_meta_tool_tier_feedback_payloads,
    load_meta_tools_not_reported,
    materialize_capture_pack,
    meta_tool_entity_id,
    register_catalog_with_meta_tools,
)


@pytest.fixture
def capture_pack(tmp_path: Path) -> TierCaptureFixturePack:
    return materialize_capture_pack(tmp_path)


@pytest.fixture(autouse=True)
def clear_tier_managers() -> Iterator[None]:
    _managers.clear()
    yield
    _managers.clear()


@pytest.fixture(autouse=True)
def clear_catalog_registry_fixture() -> Iterator[None]:
    clear_catalog_registry()
    yield
    clear_catalog_registry()


@pytest.mark.parametrize("tool_name", load_meta_tools_not_reported())
def test_is_meta_tool_matches_fixture_names(tool_name: str) -> None:
    assert is_meta_tool(tool_name) is True


@pytest.mark.parametrize("tool_name", load_meta_tools_not_reported())
def test_normalize_tools_list_excludes_meta_tools(tool_name: str) -> None:
    backend = {"name": "semble_search", "input_schema": {"type": "object"}}
    normalized = _normalize_tools_list(
        [
            backend,
            {"name": tool_name, "input_schema": {"type": "object"}},
        ],
    )
    names = {str(tool.get("name") or "") for tool in normalized}
    assert names == {"semble_search"}


@pytest.mark.parametrize("tool_name", load_meta_tools_not_reported())
def test_filter_tools_for_tier_tracking_excludes_meta_tools(
    capture_pack: TierCaptureFixturePack,
    tool_name: str,
) -> None:
    tools = [
        {"name": "semble_search", "cyt_catalog_source": "cyt_mcp"},
        {"name": tool_name, "cyt_catalog_source": "cyt_mcp"},
    ]
    tracked = filter_tools_for_tier_tracking(tools, capture_pack.config)
    names = {str(tool.get("name") or "") for tool in tracked}
    assert names == {"semble_search"}


@pytest.mark.parametrize(
    "scenario",
    load_meta_tool_tier_feedback_payloads(),
    ids=lambda item: item.id,
)
def test_record_tool_attempt_feedback_skips_meta_tools(
    capture_pack: TierCaptureFixturePack,
    scenario: MetaToolTierFeedbackScenario,
) -> None:
    record_tool_attempt_feedback(
        tool_name=scenario.tool_name,
        catalog=str(scenario.payload.get("catalog") or "cyt_mcp"),
        success=scenario.payload.get("success") is not False,
        config=capture_pack.config,
        args=scenario.payload.get("args")
        if isinstance(scenario.payload.get("args"), dict)
        else None,
        workspace=capture_pack.workspace,
    )

    from cyt.tiers.manager import get_tier_manager

    manager = get_tier_manager(capture_pack.config, workspace=capture_pack.workspace)
    entity_id = meta_tool_entity_id(scenario.tool_name)
    assert manager._states.get(("tool", entity_id)) is None


@pytest.mark.parametrize(
    "scenario",
    load_catalog_layer_registration_scenarios(),
    ids=lambda item: item.id,
)
def test_catalog_layer_registration_strips_meta_tools(
    capture_pack: TierCaptureFixturePack,
    scenario: CatalogLayerRegistrationScenario,
) -> None:
    register_catalog_with_meta_tools(
        capture_pack.workspace,
        catalog_layer=scenario.catalog_layer,
        backend_tool_name=scenario.backend_tool_name,
        meta_tool_names=scenario.meta_tool_names,
        instance_id=f"pid:{scenario.id}",
    )
    merged = catalog_for_hook("cursor", capture_pack.workspace)
    names = {str(tool.get("name") or "") for tool in merged}
    assert scenario.backend_tool_name in names
    for meta_name in scenario.meta_tool_names:
        assert meta_name not in names


def test_hydrate_runtime_cache_excludes_canonical_meta_tool_from_disk(
    tmp_path: Path,
) -> None:
    from cyt.cyt_mcp.catalog_disk import raw_catalog_content_hash, write_disk_catalog
    from cyt_mcp.catalog_build import disk_catalog_slug_for_config

    config = sample_aggregator_config(catalog_scope="workspace", workspace_root=tmp_path)
    slug = disk_catalog_slug_for_config(config)
    assert slug
    disk_tools = [
        {"name": "cyt-mcp_get-tool-definitions", "input_schema": {"type": "object"}},
        {"name": "gitnexus_cypher", "input_schema": {"type": "object"}},
    ]
    write_disk_catalog(
        slug,
        agent="cursor",
        tools=disk_tools,
        content_hash=raw_catalog_content_hash(disk_tools),
    )

    cache = RuntimeToolCache()
    assert hydrate_runtime_cache(cache, config) is True
    names = {str(entry.get("name") or "") for entry in cache.snapshot()}
    assert "gitnexus_cypher" in names
    assert "cyt-mcp_get-tool-definitions" not in names
    assert "get-tool-definitions" not in names


def test_meta_tool_entity_ids_are_distinct_from_backend_tool() -> None:
    backend_id = tool_entity_id(
        {"name": "semble_search", "cyt_catalog_source": "cyt_mcp"},
    )
    meta_id = meta_tool_entity_id("get-tool-definitions")
    assert backend_id == "cyt_mcp:semble_search"
    assert meta_id == "cyt_mcp:get-tool-definitions"
    assert backend_id != meta_id
