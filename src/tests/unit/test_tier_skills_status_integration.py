"""Integration tests for workspace skill discovery in ``cyt tiers stats``."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from _pytest.monkeypatch import MonkeyPatch

from cyt.tiers.cli import main as tiers_main
from cyt.tiers.manager import TierManager, _managers


@pytest.fixture(autouse=True)
def clear_tier_managers() -> Iterator[None]:
    _managers.clear()
    yield
    _managers.clear()


def _write_tier_config(tmp_path: Path, db_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
tools:
  tiers:
    mode: shadow
    database:
      path: {db_path}
skills:
  tiers:
    mode: shadow
""",
        encoding="utf-8",
    )


def test_tiers_status_lists_workspace_agents_skill_without_prior_tracking(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / ".git").mkdir()
    skill_dir = tmp_path / ".agents" / "skills" / "explain-simply"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: explain-simply\ndescription: Explain simply.\n---\n# Explain\n",
        encoding="utf-8",
    )

    db_path = tmp_path / "tier_state.db"
    _write_tier_config(tmp_path, db_path)
    monkeypatch.chdir(tmp_path)

    code = tiers_main(["stats", "--workspace", str(tmp_path), "--kind", "skills"])
    assert code == 0
    out = capsys.readouterr().out
    assert "explain-simply" in out
    assert "-- Effective T0 --" in out


def test_tiers_status_json_includes_discovered_workspace_skill(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / ".git").mkdir()
    skill_dir = tmp_path / ".agents" / "skills" / "explain-simply"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: explain-simply\ndescription: Explain simply.\n---\n# Explain\n",
        encoding="utf-8",
    )

    db_path = tmp_path / "tier_state.db"
    _write_tier_config(tmp_path, db_path)
    monkeypatch.chdir(tmp_path)

    code = tiers_main(
        ["stats", "--workspace", str(tmp_path), "--json", "--kind", "skills", "--name", "explain"],
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["entity_count"] == 1
    entity = payload["entities"][0]
    assert entity["name"] == "explain-simply"
    assert entity["effective_tier"] == "T0"
    assert entity["base_tier"] == "T0"


def test_tier_manager_status_merges_workspace_skills(
    tmp_path: Path,
) -> None:
    from cyt.config import load_config

    base_config = load_config()
    (tmp_path / ".git").mkdir()
    skill_dir = tmp_path / ".agents" / "skills" / "explain-simply"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: explain-simply\ndescription: Explain simply.\n---\n# Explain\n",
        encoding="utf-8",
    )

    config = dict(base_config)
    tools = dict(config.get("tools") or {})
    tools["tiers"] = {
        "mode": "shadow",
        "database": {"path": str(tmp_path / "tier_state.db")},
    }
    config["tools"] = tools

    manager = TierManager(tmp_path, str(tmp_path / "tier_state.db"))
    try:
        status = manager.status(config, agent="cursor")
    finally:
        manager.close()

    skills = status["skills"]
    dormant = skills["by_tier"]["T0"]
    names = {item.get("name") for item in dormant}
    assert "explain-simply" in names
