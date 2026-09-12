"""Integration tests: ``cyt tiers stats`` with fixture-backed tool catalog and skills."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from _pytest.monkeypatch import MonkeyPatch

from cyt.cyt_mcp.catalog import clear_cyt_mcp_catalog_cache
from cyt.tiers.cli import main as tiers_main
from cyt.tiers.manager import _managers
from cyt.tools.master_catalog import (
    clear_master_catalog_cache,
    get_master_tool_catalog,
    rebuild_master_catalog,
)
from tests.support.tiers_stats_fixtures import (
    TiersStatsFixturePack,
    load_scenario,
    materialize_fixture_pack,
    patch_cyt_mcp_paths,
    tier_stats_config,
    write_cyt_mcp_disk_catalog,
)


@pytest.fixture(autouse=True)
def clear_tier_managers() -> Iterator[None]:
    _managers.clear()
    yield
    _managers.clear()


@pytest.fixture
def fixture_pack(tmp_path: Path) -> TiersStatsFixturePack:
    return materialize_fixture_pack(tmp_path)


@pytest.fixture
def disk_catalog_pack(
    fixture_pack: TiersStatsFixturePack,
    monkeypatch: MonkeyPatch,
) -> Iterator[TiersStatsFixturePack]:
    patch_cyt_mcp_paths(monkeypatch, fixture_pack)
    clear_master_catalog_cache()
    clear_cyt_mcp_catalog_cache()
    write_cyt_mcp_disk_catalog(fixture_pack)
    yield fixture_pack
    clear_master_catalog_cache()
    clear_cyt_mcp_catalog_cache()


def test_tiers_stats_json_overview_lists_fixture_tools_and_skills(
    disk_catalog_pack: TiersStatsFixturePack,
    monkeypatch: MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(disk_catalog_pack.workspace)
    code = tiers_main(["stats", "--workspace", str(disk_catalog_pack.workspace), "--json"])
    assert code == 0

    payload = json.loads(capsys.readouterr().out)
    expected = load_scenario().expected

    tools = payload["tools"]
    assert tools["histogram"] == expected["tools_by_tier"]
    assert sum(tools["histogram"].values()) == expected["tools_total"]
    assert payload["overview"]["troubleshooting"]["catalog_tool_count"] == expected["tools_total"]

    skills = payload["skills"]
    skill_names = {row.get("name") for row in skills["by_tier"]["T0"]}
    assert len(skill_names) >= expected["skills_min_total"]
    assert set(expected["skill_names"]).issubset(skill_names)

    tier_stats = payload["overview"]["tier_statistics"]["tools"]["totals"]
    assert tier_stats["count"] == expected["tools_total"]


def test_tiers_stats_overview_text_shows_nonzero_tool_total(
    disk_catalog_pack: TiersStatsFixturePack,
    monkeypatch: MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(disk_catalog_pack.workspace)
    code = tiers_main(["stats", "--workspace", str(disk_catalog_pack.workspace)])
    assert code == 0
    out = capsys.readouterr().out

    expected = load_scenario().expected
    assert "=== tools ===" in out
    assert f"Total  {expected['tools_total']}" in out
    assert "=== skills ===" in out
    skills_block = out.split("=== skills ===", 1)[1].split("injected:", 1)[0]
    assert "Total  2" in skills_block


def test_tiers_stats_verbose_lists_fixture_mcp_servers(
    disk_catalog_pack: TiersStatsFixturePack,
    monkeypatch: MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(disk_catalog_pack.workspace)
    code = tiers_main(["stats", "--workspace", str(disk_catalog_pack.workspace), "--verbose"])
    assert code == 0
    out = capsys.readouterr().out

    for server in load_scenario().expected["mcp_server_names"]:
        assert server in out


def test_tiers_stats_filter_hot_tool_from_fixture_catalog(
    disk_catalog_pack: TiersStatsFixturePack,
    monkeypatch: MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(disk_catalog_pack.workspace)
    code = tiers_main(
        [
            "stats",
            "--workspace",
            str(disk_catalog_pack.workspace),
            "--json",
            "--kind",
            "tools",
            "--tier",
            "T3",
        ],
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["entity_count"] == 1
    assert payload["entities"][0]["entity_id"] == load_scenario().expected["hot_tool_entity_id"]


def test_tiers_stats_blocking_catalog_survives_concurrent_rebuild(
    disk_catalog_pack: TiersStatsFixturePack,
    monkeypatch: MonkeyPatch,
) -> None:
    config = tier_stats_config(disk_catalog_pack)

    def background_rebuild() -> None:
        rebuild_master_catalog(config, blocking=True)

    thread = threading.Thread(target=background_rebuild)
    thread.start()
    time.sleep(0.01)
    catalog = get_master_tool_catalog(config, blocking=True)
    thread.join(timeout=5.0)

    expected_total = load_scenario().expected["tools_total"]
    assert catalog is not None
    assert len(catalog) == expected_total


def test_tiers_stats_after_cache_clear_loads_tools_from_disk(
    disk_catalog_pack: TiersStatsFixturePack,
    monkeypatch: MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    clear_master_catalog_cache()
    clear_cyt_mcp_catalog_cache()
    monkeypatch.chdir(disk_catalog_pack.workspace)

    code = tiers_main(["stats", "--workspace", str(disk_catalog_pack.workspace), "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["tools"]["histogram"]["T0"] == 2
    assert payload["tools"]["histogram"]["T3"] == 1
