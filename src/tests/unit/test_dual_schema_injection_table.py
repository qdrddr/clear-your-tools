"""Parametrized tests for section 4 backend-aware injection hint table rows."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from cyt.cyt_mcp.catalog import clear_cyt_mcp_catalog_cache
from cyt.injection.pre_exposed import filter_pre_exposed_tools
from cyt.pruners.tools_filter import filter_tools_for_query
from cyt.tiers.adapters.tools import apply_tool_tiers, tool_entity_id
from cyt.tiers.manager import _managers
from cyt.tiers.models import Tier
from cyt.tools.inject import format_tool_item
from cyt.tools.master_catalog import clear_master_catalog_cache
from tests.support.dual_schema_injection_fixtures import (
    DualSchemaFixturePack,
    InjectionTableCase,
    assert_injection_table_format,
    build_injection_table_tool,
    injection_table_cases_by_expected,
    injection_table_format_cases,
    live_tier_config,
    load_injection_table_cases,
    load_tools_catalog,
    materialize_fixture_pack,
    patch_paths,
    seed_tool_tiers,
    tier_map_for_injection_table_prune,
    tool_by_name,
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


def test_injection_table_has_all_section_4_rows() -> None:
    cases = load_injection_table_cases()
    assert len(cases) == 33
    assert len({case.id for case in cases}) == 33
    catalog = load_tools_catalog()
    for case in cases:
        tool_by_name(case.tool_ref, catalog)

    expected_kinds = {
        "absent_excluded",
        "absent_prune",
        "absent_dedup",
        *{
            "description_only",
            "hint",
            "explicit_empty_schema",
            "required_schema",
            "optional_only_schema",
            "required_and_optional_schema",
            "full_backend_schema",
        },
    }
    assert {case.expected for case in cases} == expected_kinds
    prune_cases = injection_table_cases_by_expected("absent_prune")
    assert len(prune_cases) == 8
    for case in prune_cases:
        assert case.prune_query
        assert case.prune_decoy_tool_ref


@pytest.mark.parametrize("case", injection_table_format_cases(), ids=lambda case: case.id)
def test_injection_table_format_outcomes(case: InjectionTableCase) -> None:
    tool = build_injection_table_tool(case)
    item = format_tool_item(tool)
    assert_injection_table_format(item, case)


@pytest.mark.parametrize(
    "case",
    injection_table_cases_by_expected("absent_dedup"),
    ids=lambda case: case.id,
)
def test_injection_table_pre_exposure_dedup(case: InjectionTableCase) -> None:
    tool = build_injection_table_tool(case)
    fragment = format_tool_item(tool)
    filtered = filter_pre_exposed_tools([tool], fragment)
    assert filtered == []


def test_injection_table_t0_excluded_from_prune_pool() -> None:
    case = next(
        case for case in load_injection_table_cases() if case.id == "t0_excluded_from_prune_pool"
    )
    tool = tool_by_name(case.tool_ref)
    entity_id = tool_entity_id(tool)
    result = apply_tool_tiers([tool], tier_for_tool={entity_id: Tier.DORMANT}, apply=True)
    assert entity_id in result.excluded_t0
    assert case.tool_ref not in {str(item.get("name") or "") for item in result.eligible_tools}


@pytest.mark.parametrize(
    "case",
    injection_table_cases_by_expected("absent_prune"),
    ids=lambda case: case.id,
)
def test_injection_table_prune_drop_absent_from_pipeline(
    disk_catalog_pack: DualSchemaFixturePack,
    case: InjectionTableCase,
) -> None:
    from cyt.tiers.manager import TierManager

    assert case.prune_query is not None
    assert case.prune_decoy_tool_ref is not None

    pack = disk_catalog_pack
    seed_tool_tiers(pack, tier_map_for_injection_table_prune(case))
    config = live_tier_config(pack)
    manager = TierManager(pack.workspace, str(pack.db_path))
    try:
        with patch("cyt.pruners.tools_filter.get_tier_manager", return_value=manager):
            result = filter_tools_for_query(
                pack.tools,
                case.prune_query,
                ["bm25"],
                config=config,
                for_hook=True,
            )
    finally:
        manager.close()

    kept = {str(tool.get("name") or "") for tool in (result.tools or [])}
    assert case.tool_ref not in kept
    assert case.prune_decoy_tool_ref in kept
