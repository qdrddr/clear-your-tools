"""Integration tests: ``cyt tiers stats`` hides skills when skills.enabled is false."""

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
    seed_skill_tier_states,
    tier_stats_config,
    write_cyt_mcp_disk_catalog,
)


@pytest.fixture(autouse=True)
def clear_tier_managers() -> Iterator[None]:
    _managers.clear()
    yield
    _managers.clear()


@pytest.fixture
def skills_disabled_catalog_pack(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> Iterator[TiersStatsFixturePack]:
    pack = materialize_fixture_pack(tmp_path)
    seed_skill_tier_states(
        pack,
        {
            "skill:create-hook": {
                "stable_tier": "ACTIVE",
                "effective_tier": "ACTIVE",
                "stats": {"injected": 10, "used": 5, "candidates": 20},
            },
        },
    )
    patch_cyt_mcp_paths(monkeypatch, pack, skills_enabled=False)
    write_cyt_mcp_disk_catalog(pack)
    yield pack


def test_tiers_stats_json_omits_skills_when_disabled(
    skills_disabled_catalog_pack: TiersStatsFixturePack,
    monkeypatch: MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(skills_disabled_catalog_pack.workspace)
    code = tiers_main(
        ["stats", "--workspace", str(skills_disabled_catalog_pack.workspace), "--json"],
    )
    assert code == 0

    payload = json.loads(capsys.readouterr().out)
    expected = load_scenario().expected

    assert "skills" not in payload
    overview = payload.get("overview") or {}
    tier_stats = overview.get("tier_statistics") or {}
    assert "skills" not in tier_stats
    assert "skill_directories" not in overview
    assert "db_skill_entities" not in (overview.get("troubleshooting") or {})

    tools = payload["tools"]
    assert sum(tools["histogram"].values()) == expected["tools_total"]


def test_tiers_stats_text_hides_skills_and_historical_skill_lines(
    skills_disabled_catalog_pack: TiersStatsFixturePack,
    monkeypatch: MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(skills_disabled_catalog_pack.workspace)
    code = tiers_main(["stats", "--workspace", str(skills_disabled_catalog_pack.workspace)])
    assert code == 0
    out = capsys.readouterr().out

    expected = load_scenario().expected
    assert "=== tools ===" in out
    assert f"Total  {expected['tools_total']}" in out
    assert "=== skills ===" not in out
    assert "skills injected=" not in out
    assert "skills used=" not in out
    assert "Historical signals (decayed sum):" in out
    assert "tools injected=" in out
    assert "tools used=" in out


def test_tiers_stats_kind_skills_errors_when_disabled(
    skills_disabled_catalog_pack: TiersStatsFixturePack,
    monkeypatch: MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(skills_disabled_catalog_pack.workspace)
    code = tiers_main(
        [
            "stats",
            "--workspace",
            str(skills_disabled_catalog_pack.workspace),
            "--kind",
            "skills",
        ],
    )
    assert code == 2
    assert "skills.enabled is false" in capsys.readouterr().err


def test_tier_state_still_tracks_skills_when_display_disabled(
    skills_disabled_catalog_pack: TiersStatsFixturePack,
) -> None:
    config = tier_stats_config(skills_disabled_catalog_pack, skills_enabled=False)
    manager = get_tier_manager(config, workspace=skills_disabled_catalog_pack.workspace)
    manager.record_skill_used("skill:context7", config=config)
    state = manager._states.get(("skill", "skill:context7"))
    assert state is not None
    assert state.stats.used >= 1.0
