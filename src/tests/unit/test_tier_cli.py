"""Tests for cyt tiers CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from _pytest.monkeypatch import MonkeyPatch

from cyt.tiers.cli import main as tiers_main
from cyt.tiers.manager import _managers
from cyt.tiers.models import EffectiveStats, EntityTierState, Tier, TierProject
from cyt.tiers.store import TierStore


def _mock_tracked_catalog(monkeypatch: MonkeyPatch, *tool_names: str) -> None:
    catalog = [{"name": name, "cyt_catalog_source": "cyt_mcp"} for name in tool_names]
    monkeypatch.setattr(
        "cyt.tools.master_catalog.get_master_tool_catalog",
        lambda config, blocking=False: catalog,
    )


def test_tiers_status_json(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    (tmp_path / ".git").mkdir()
    db_path = tmp_path / "tier_state.db"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
tools:
  tiers:
    enabled: false
    shadow: true
    database:
      path: {db_path}
skills:
  tiers:
    enabled: false
    shadow: true
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    _managers.clear()
    code = tiers_main(["stats", "--workspace", str(tmp_path), "--json"])
    assert code == 0


def test_tiers_status_json_includes_entity_details(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / ".git").mkdir()
    db_path = tmp_path / "tier_state.db"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
tools:
  tiers:
    enabled: false
    shadow: true
    database:
      path: {db_path}
skills:
  tiers:
    enabled: false
    shadow: true
""",
        encoding="utf-8",
    )
    store = TierStore.open(str(db_path))
    try:
        project_id = store.get_or_create_project(str(tmp_path))
        project = TierProject(project_id=project_id, root_path=tmp_path)
        store.upsert_entity_state(
            project,
            EntityTierState(
                entity_id="cyt_mcp:search",
                kind="tool",
                stable_tier=Tier.ACTIVE,
                effective_tier=Tier.HOT,
                overlap_tier=Tier.ACTIVE,
                stats=EffectiveStats(candidates=120, injected=45, used=12),
            ),
        )
        store.upsert_entity_state(
            project,
            EntityTierState(
                entity_id="skill:explore",
                kind="skill",
                stable_tier=Tier.COLD,
                effective_tier=Tier.COLD,
                stats=EffectiveStats(candidates=8, injected=3, used=1),
            ),
        )
    finally:
        store.close()

    _mock_tracked_catalog(monkeypatch, "search")
    monkeypatch.chdir(tmp_path)
    _managers.clear()
    code = tiers_main(["stats", "--workspace", str(tmp_path), "--json"])
    assert code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    tools = payload["tools"]
    assert tools["enabled"] is False
    assert tools["shadow"] is True
    assert "config" in tools
    assert tools["histogram"]["T3"] == 1

    hot_tools = tools["by_tier"]["T3"]
    assert len(hot_tools) == 1
    search = hot_tools[0]
    assert search["entity_id"] == "cyt_mcp:search"
    assert search["base_tier"] == "T2"
    assert search["effective_tier"] == "T3"
    assert search["temporary_tier"] == "T3"
    assert search["stats"]["candidates"] == 120.0
    assert search["scores"]["demand"] > 0

    skills = payload["skills"]
    assert skills["histogram"]["T1"] == 1
    assert skills["by_tier"]["T1"][0]["entity_id"] == "skill:explore"

    hist_total = sum(tools["histogram"].values())
    by_tier_total = sum(len(items) for items in tools["by_tier"].values())
    assert hist_total == by_tier_total

    assert payload["mode"] == "overview"
    assert isinstance(payload.get("overview"), dict)
    assert "mcp_servers" in payload["overview"]


def test_tiers_status_json_filters_by_kind_and_name(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / ".git").mkdir()
    db_path = tmp_path / "tier_state.db"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
tools:
  tiers:
    enabled: false
    shadow: true
    database:
      path: {db_path}
skills:
  tiers:
    enabled: false
    shadow: true
""",
        encoding="utf-8",
    )
    store = TierStore.open(str(db_path))
    try:
        project_id = store.get_or_create_project(str(tmp_path))
        project = TierProject(project_id=project_id, root_path=tmp_path)
        store.upsert_entity_state(
            project,
            EntityTierState(
                entity_id="cyt_mcp:search",
                kind="tool",
                stable_tier=Tier.ACTIVE,
                effective_tier=Tier.HOT,
                stats=EffectiveStats(injected=1),
            ),
        )
        store.upsert_entity_state(
            project,
            EntityTierState(
                entity_id="skill:explore",
                kind="skill",
                stable_tier=Tier.COLD,
                effective_tier=Tier.COLD,
            ),
        )
    finally:
        store.close()

    _mock_tracked_catalog(monkeypatch, "search")
    monkeypatch.chdir(tmp_path)
    _managers.clear()
    code = tiers_main(
        ["stats", "--workspace", str(tmp_path), "--json", "--kind", "tools", "--tier", "3"],
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["entity_count"] == 1
    assert payload["entity_total"] == 1
    assert payload["filters"] == {"kind": "tools", "tier": "T3"}
    assert payload["entities"][0]["entity_id"] == "cyt_mcp:search"
    assert payload["mode"] == "filtered"
    assert "overview" not in payload
    assert "skills" not in payload
    assert "tools" not in payload
    assert "histogram" not in payload
    assert len(payload["entities"]) == 1


def test_tiers_status_json_filters_tools_by_t0_case_insensitive(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / ".git").mkdir()
    db_path = tmp_path / "tier_state.db"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
tools:
  tiers:
    enabled: false
    shadow: true
    database:
      path: {db_path}
skills:
  tiers:
    enabled: false
    shadow: true
""",
        encoding="utf-8",
    )
    store = TierStore.open(str(db_path))
    try:
        project_id = store.get_or_create_project(str(tmp_path))
        project = TierProject(project_id=project_id, root_path=tmp_path)
        store.upsert_entity_state(
            project,
            EntityTierState(
                entity_id="cyt_mcp:active_tool",
                kind="tool",
                stable_tier=Tier.ACTIVE,
                effective_tier=Tier.ACTIVE,
            ),
        )
    finally:
        store.close()

    _mock_tracked_catalog(monkeypatch, "dormant_tool", "active_tool")
    monkeypatch.chdir(tmp_path)
    _managers.clear()
    code = tiers_main(
        [
            "stats",
            "--workspace",
            str(tmp_path),
            "--json",
            "--kind",
            "tools",
            "--tier",
            "t0",
        ],
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["filters"] == {"kind": "tools", "tier": "T0"}
    assert payload["entity_total"] == 2
    assert payload["entity_count"] == 1
    assert payload["entities"][0]["entity_id"] == "cyt_mcp:dormant_tool"
    assert payload["entities"][0]["effective_tier"] == "T0"


def test_tiers_list_matches_stats_without_filters(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / ".git").mkdir()
    db_path = tmp_path / "tier_state.db"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
tools:
  tiers:
    enabled: false
    shadow: true
    database:
      path: {db_path}
skills:
  tiers:
    enabled: false
    shadow: true
""",
        encoding="utf-8",
    )
    _mock_tracked_catalog(monkeypatch)
    monkeypatch.chdir(tmp_path)
    _managers.clear()

    stats_code = tiers_main(["stats", "--workspace", str(tmp_path)])
    stats_out = capsys.readouterr().out
    assert stats_code == 0

    list_code = tiers_main(["list", "--workspace", str(tmp_path)])
    list_out = capsys.readouterr().out
    assert list_code == 0
    assert list_out == stats_out


def test_tiers_status_default_human_shows_overview(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / ".git").mkdir()
    db_path = tmp_path / "tier_state.db"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
tools:
  tiers:
    enabled: false
    shadow: true
    database:
      path: {db_path}
skills:
  tiers:
    enabled: false
    shadow: true
""",
        encoding="utf-8",
    )
    store = TierStore.open(str(db_path))
    try:
        project_id = store.get_or_create_project(str(tmp_path))
        project = TierProject(project_id=project_id, root_path=tmp_path)
        store.upsert_entity_state(
            project,
            EntityTierState(
                entity_id="cyt_mcp:search",
                kind="tool",
                stable_tier=Tier.ACTIVE,
                effective_tier=Tier.HOT,
                stats=EffectiveStats(injected=1, used=1),
            ),
        )
        store.upsert_entity_state(
            project,
            EntityTierState(
                entity_id="skill:explore",
                kind="skill",
                stable_tier=Tier.COLD,
                effective_tier=Tier.COLD,
            ),
        )
    finally:
        store.close()

    _mock_tracked_catalog(monkeypatch, "search")
    monkeypatch.chdir(tmp_path)
    _managers.clear()
    code = tiers_main(["stats", "--workspace", str(tmp_path)])
    assert code == 0
    out = capsys.readouterr().out
    assert f"project_id: {project_id}" in out
    assert f"root_path: {tmp_path}" in out
    assert "=== tools ===" in out
    assert "=== skills ===" in out
    assert "Count  Temp  Tokens" in out
    assert "=== mcp servers ===" not in out
    assert "=== skill directories ===" not in out
    assert "=== troubleshooting ===" not in out
    assert "search" not in out
    assert "explore" not in out


def test_tiers_status_human_lists_entities(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / ".git").mkdir()
    db_path = tmp_path / "tier_state.db"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
tools:
  tiers:
    enabled: false
    shadow: true
    database:
      path: {db_path}
skills:
  tiers:
    enabled: false
    shadow: true
""",
        encoding="utf-8",
    )
    store = TierStore.open(str(db_path))
    try:
        project_id = store.get_or_create_project(str(tmp_path))
        project = TierProject(project_id=project_id, root_path=tmp_path)
        store.upsert_entity_state(
            project,
            EntityTierState(
                entity_id="cyt_mcp:search",
                kind="tool",
                stable_tier=Tier.ACTIVE,
                effective_tier=Tier.HOT,
                stats=EffectiveStats(injected=2, used=1),
            ),
        )
    finally:
        store.close()

    _mock_tracked_catalog(monkeypatch, "search")
    monkeypatch.chdir(tmp_path)
    _managers.clear()
    code = tiers_main(["stats", "--workspace", str(tmp_path), "--name", "search"])
    assert code == 0
    out = capsys.readouterr().out
    assert "entities: showing 1 of" in out
    assert "[tool] search" in out
    assert "effective=T3" in out
    assert "stats:" in out


def test_tiers_status_invalid_tier(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / ".git").mkdir()
    config_path = tmp_path / "config.yaml"
    config_path.write_text("tools:\n  tiers:\n    enabled: false\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    _managers.clear()
    code = tiers_main(["stats", "--workspace", str(tmp_path), "--tier", "bad"])
    assert code == 2
    assert "invalid --tier" in capsys.readouterr().err


def test_tiers_status_requires_project(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "config.yaml"
    config_path.write_text("tools:\n  tiers:\n    enabled: false\n", encoding="utf-8")
    monkeypatch.setenv("CYT_CONFIG", str(config_path))
    code = tiers_main(["stats"])
    assert code == 2
