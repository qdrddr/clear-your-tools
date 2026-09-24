"""Integration tests: ``cyt tiers stats`` hides tools when tools.enabled is false."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from _pytest.monkeypatch import MonkeyPatch

from cyt.tiers.cli import main as tiers_main
from cyt.tiers.manager import _managers, get_tier_manager
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
def tools_disabled_catalog_pack(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> Iterator[TiersStatsFixturePack]:
    pack = materialize_fixture_pack(tmp_path)
    patch_cyt_mcp_paths(monkeypatch, pack, tools_enabled=False)
    write_cyt_mcp_disk_catalog(pack)
    yield pack


def test_tiers_stats_json_omits_tools_when_disabled(
    tools_disabled_catalog_pack: TiersStatsFixturePack,
    monkeypatch: MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tools_disabled_catalog_pack.workspace)
    code = tiers_main(
        ["stats", "--workspace", str(tools_disabled_catalog_pack.workspace), "--json"],
    )
    assert code == 0

    payload = json.loads(capsys.readouterr().out)
    expected = load_scenario().expected

    assert "tools" not in payload
    overview = payload.get("overview") or {}
    tier_stats = overview.get("tier_statistics") or {}
    assert "tools" not in tier_stats
    assert "mcp_servers" not in overview
    assert "db_tool_entities" not in (overview.get("troubleshooting") or {})

    skills = payload["skills"]
    assert sum(skills["histogram"].values()) >= expected["skills_min_total"]


def test_tiers_stats_text_hides_tools_and_historical_tool_lines(
    tools_disabled_catalog_pack: TiersStatsFixturePack,
    monkeypatch: MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tools_disabled_catalog_pack.workspace)
    code = tiers_main(["stats", "--workspace", str(tools_disabled_catalog_pack.workspace)])
    assert code == 0
    out = capsys.readouterr().out

    assert "=== skills ===" in out
    assert "skills injected=" in out
    assert "=== tools ===" not in out
    assert "tools injected=" not in out
    assert "tools used=" not in out
    assert "Historical signals (decayed sum):" in out
    assert "skills used=" in out


def test_tiers_stats_kind_tools_errors_when_disabled(
    tools_disabled_catalog_pack: TiersStatsFixturePack,
    monkeypatch: MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tools_disabled_catalog_pack.workspace)
    code = tiers_main(
        [
            "stats",
            "--workspace",
            str(tools_disabled_catalog_pack.workspace),
            "--kind",
            "tools",
        ],
    )
    assert code == 2
    assert "tools.enabled is false" in capsys.readouterr().err


def test_tiers_stats_server_filter_errors_when_disabled(
    tools_disabled_catalog_pack: TiersStatsFixturePack,
    monkeypatch: MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tools_disabled_catalog_pack.workspace)
    code = tiers_main(
        [
            "stats",
            "--workspace",
            str(tools_disabled_catalog_pack.workspace),
            "--server",
            "context-mode",
        ],
    )
    assert code == 2
    assert "tools.enabled is false" in capsys.readouterr().err


def test_tier_state_still_tracks_tools_when_display_disabled(
    tools_disabled_catalog_pack: TiersStatsFixturePack,
) -> None:
    config = tier_stats_config(tools_disabled_catalog_pack, tools_enabled=False)
    manager = get_tier_manager(config, workspace=tools_disabled_catalog_pack.workspace)
    manager.record_tool_used(
        {"name": "context7__resolve-library-id", "cyt_catalog_source": "cyt_mcp"},
        config=config,
    )
    state = manager._states.get(("tool", "cyt_mcp:context7__resolve-library-id"))
    assert state is not None
    assert state.stats.used >= 1.0
