"""Tests for tiers status overview mode and --path filtering."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from _pytest.monkeypatch import MonkeyPatch

from cyt.tiers.cli import main as tiers_main
from cyt.tiers.manager import _managers
from cyt.tiers.models import EntityTierState, Tier, TierProject
from cyt.tiers.status_detail import (
    filter_skill_detail_by_path,
    validate_status_path_filter,
)
from cyt.tiers.status_overview import (
    _path_display,
    _short_path,
    build_status_overview,
    format_overview_text,
)
from cyt.tiers.status_view import (
    StatusFilters,
    entity_matches_scope,
    filter_status_entities,
    resolve_status_path_filter,
)
from cyt.tiers.store import TierStore


def _mock_catalog(
    monkeypatch: MonkeyPatch,
    tools: list[dict[str, Any]],
) -> None:
    monkeypatch.setattr(
        "cyt.tools.master_catalog.get_master_tool_catalog",
        lambda config, blocking=False: tools,
    )
    monkeypatch.setattr(
        "cyt.tiers.status_overview.get_master_tool_catalog",
        lambda config, blocking=False: tools,
        raising=False,
    )


def test_status_filters_overview_mode() -> None:
    assert StatusFilters().overview_mode is True
    assert StatusFilters(kind="tools").overview_mode is False
    assert StatusFilters(path="/tmp/skills").overview_mode is False
    assert StatusFilters(scope="user").overview_mode is False


def test_short_path_substitutes_home_prefix() -> None:
    home = Path.home()
    absolute = home / ".config" / "cyt" / "mcp" / "cursor.json"
    assert (
        _path_display(absolute, scope="user", workspace_root=None)
        == "~/.config/cyt/mcp/cursor.json"
    )
    assert _short_path(str(absolute)) == "~/.config/cyt/mcp/cursor.json"


def test_path_display_workspace_relative_to_root(tmp_path: Path) -> None:
    ws_file = tmp_path / ".agents" / "cyt" / "config" / "mcp" / "cursor.json"
    ws_file.parent.mkdir(parents=True)
    ws_file.touch()
    assert (
        _path_display(ws_file, scope="workspace", workspace_root=tmp_path)
        == ".agents/cyt/config/mcp/cursor.json"
    )


def test_format_overview_text_total_before_path(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _mock_catalog(
        monkeypatch,
        [{"name": "ctx_execute", "server_key": "context-mode", "cyt_catalog_source": "cyt_mcp"}],
    )
    monkeypatch.setattr(
        "cyt.tools.master_catalog.master_catalog_health_snapshot",
        lambda config: {"configured_sources": ["cyt_mcp"], "catalog_tool_count": 1},
    )
    payload = {
        "project_id": 1,
        "root_path": str(tmp_path),
        "agent": "cursor",
        "overview": build_status_overview(
            {
                "tools": {"enabled": False, "shadow": True},
                "skills": {"enabled": False, "shadow": True},
            },
            config={},
            workspace_root=tmp_path,
            agent="cursor",
        ),
    }
    text = format_overview_text(payload)
    for line in text.splitlines():
        if line.startswith("Scope") and "Path" in line:
            assert line.index("Total") < line.index("Path")


def test_filter_status_entities_by_scope() -> None:
    entities = [
        {"kind": "tool", "entity_id": "cyt_mcp:a", "scope": "workspace", "effective_tier": "T2"},
        {"kind": "tool", "entity_id": "cyt_mcp:b", "scope": "user", "effective_tier": "T2"},
        {"kind": "skill", "entity_id": "skill:c", "scope": "workspace", "effective_tier": "T1"},
    ]
    filtered = filter_status_entities(entities, StatusFilters(kind="tools", scope="workspace"))
    assert len(filtered) == 1
    assert filtered[0]["entity_id"] == "cyt_mcp:a"
    assert entity_matches_scope({"scope": "user"}, "user") is True
    assert entity_matches_scope({"scope": "workspace"}, "user") is False


def test_resolve_status_path_filter_relative_to_workspace(tmp_path: Path) -> None:
    skills_dir = tmp_path / ".agents" / "skills"
    skills_dir.mkdir(parents=True)
    resolved, display, error = resolve_status_path_filter(".agents/skills", tmp_path)
    assert error is None
    assert display == ".agents/skills"
    assert resolved == skills_dir.resolve()


def test_build_status_overview_groups_servers(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    catalog = [
        {"name": "ctx_execute", "server_key": "context-mode", "cyt_catalog_source": "cyt_mcp"},
        {"name": "ctx_search", "server_key": "context-mode", "cyt_catalog_source": "cyt_mcp"},
        {"name": "gitnexus_query", "server_key": "gitnexus", "cyt_catalog_source": "cyt_mcp"},
    ]
    _mock_catalog(monkeypatch, catalog)
    monkeypatch.setattr(
        "cyt.tools.master_catalog.master_catalog_health_snapshot",
        lambda config: {
            "configured_sources": ["cyt_mcp"],
            "catalog_tool_count": 3,
        },
    )

    status = {
        "tools": {
            "enabled": False,
            "shadow": True,
            "tracked_catalog_tool_count": 3,
            "histogram": {"T0": 0, "T1": 0, "T2": 3, "T3": 0, "T4": 0},
        },
        "skills": {
            "enabled": False,
            "shadow": True,
            "histogram": {"T0": 0, "T1": 0, "T2": 0, "T3": 0, "T4": 0},
        },
        "epoch_id": 1,
        "session_id": 2,
    }
    overview = build_status_overview(
        status,
        config={},
        workspace_root=tmp_path,
        agent="cursor",
    )
    servers = overview.get("mcp_servers")
    assert isinstance(servers, list)
    assert len(servers) == 2
    names = {row["name"] for row in servers if isinstance(row, dict)}
    assert names == {"context-mode", "gitnexus"}
    counts = {row["name"]: row["tool_count"] for row in servers if isinstance(row, dict)}
    assert counts["context-mode"] == 2
    assert counts["gitnexus"] == 1


def test_format_overview_text_omits_individual_tools(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    catalog = [
        {"name": "ctx_execute", "server_key": "context-mode", "cyt_catalog_source": "cyt_mcp"},
    ]
    _mock_catalog(monkeypatch, catalog)
    monkeypatch.setattr(
        "cyt.tools.master_catalog.master_catalog_health_snapshot",
        lambda config: {"configured_sources": ["cyt_mcp"], "catalog_tool_count": 1},
    )
    payload = {
        "project_id": 7,
        "root_path": str(tmp_path),
        "agent": "cursor",
        "overview": build_status_overview(
            {
                "tools": {"enabled": False, "shadow": True},
                "skills": {"enabled": False, "shadow": True},
            },
            config={},
            workspace_root=tmp_path,
            agent="cursor",
        ),
    }
    text = format_overview_text(payload)
    assert "=== mcp servers ===" in text
    assert "=== skill directories ===" in text
    assert "=== troubleshooting ===" in text
    assert "ctx_execute" not in text
    assert "context-mode" in text


def test_tiers_status_default_shows_overview_not_tools(
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
                entity_id="cyt_mcp:ctx_execute",
                kind="tool",
                stable_tier=Tier.ACTIVE,
                effective_tier=Tier.HOT,
            ),
        )
    finally:
        store.close()

    _mock_catalog(
        monkeypatch,
        [{"name": "ctx_execute", "server_key": "context-mode", "cyt_catalog_source": "cyt_mcp"}],
    )
    monkeypatch.chdir(tmp_path)
    _managers.clear()
    code = tiers_main(["status", "--workspace", str(tmp_path)])
    assert code == 0
    out = capsys.readouterr().out
    assert f"project_id: {project_id}" in out
    assert "=== mcp servers ===" in out
    assert "=== mcp config files ===" in out
    assert "=== skill directories ===" in out
    assert "=== troubleshooting ===" in out
    assert "=== tools ===" not in out
    assert "ctx_execute" not in out


def test_tiers_status_path_lists_skills_under_directory(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    workspace_skills = tmp_path / ".agents" / "skills" / "workspace-skill"
    workspace_skills.mkdir(parents=True)
    (workspace_skills / "SKILL.md").write_text(
        "---\nname: workspace-skill\n---\n",
        encoding="utf-8",
    )

    user_skills = fake_home / ".cursor" / "skills" / "user-skill"
    user_skills.mkdir(parents=True)
    (user_skills / "SKILL.md").write_text("---\nname: user-skill\n---\n", encoding="utf-8")

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
    _mock_catalog(monkeypatch, [])
    monkeypatch.chdir(tmp_path)
    _managers.clear()

    code = tiers_main(
        ["status", "--workspace", str(tmp_path), "--path", str(fake_home / ".cursor" / "skills")],
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "user-skill" in out
    assert "workspace-skill" not in out


def test_tiers_status_path_rejects_outside_discovery_roots(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / ".git").mkdir()
    outside = tmp_path / "outside" / "skills"
    outside.mkdir(parents=True)
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
    _mock_catalog(monkeypatch, [])
    monkeypatch.chdir(tmp_path)
    _managers.clear()

    code = tiers_main(["status", "--workspace", str(tmp_path), "--path", str(outside)])
    assert code == 2
    err = capsys.readouterr().err
    assert "outside configured skill discovery" in err


def test_validate_status_path_filter_accepts_subdirectory(tmp_path: Path) -> None:
    root = tmp_path / ".agents" / "skills"
    sub = root / "pack"
    sub.mkdir(parents=True)
    message = validate_status_path_filter(
        sub,
        config={},
        workspace_root=tmp_path,
        agent="cursor",
    )
    assert message is None


def test_filter_skill_detail_by_path_discovers_disk_only(
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / ".agents" / "skills" / "disk-only"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: disk-only\n---\n", encoding="utf-8")
    detail = {
        "histogram": dict.fromkeys([f"T{i}" for i in range(5)], 0),
        "by_tier": {f"T{i}": [] for i in range(5)},
    }
    from cyt.tiers.config import tier_section_config

    filtered = filter_skill_detail_by_path(
        detail,
        path_root=tmp_path / ".agents" / "skills",
        config={},
        workspace_root=tmp_path,
        agent="cursor",
        states={},
        cfg=tier_section_config({}, kind="skill"),
        session_id=0,
    )
    names = [
        item.get("name")
        for items in filtered["by_tier"].values()
        for item in items
        if isinstance(item, dict)
    ]
    assert "disk-only" in names
