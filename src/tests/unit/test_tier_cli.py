"""Tests for cyt tiers CLI."""

from __future__ import annotations

from pathlib import Path

from _pytest.monkeypatch import MonkeyPatch

from cyt.tiers.cli import main as tiers_main


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
    monkeypatch.setenv("CYT_CONFIG", str(config_path))
    code = tiers_main(["status", "--workspace", str(tmp_path), "--json"])
    assert code == 0


def test_tiers_status_requires_project(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "config.yaml"
    config_path.write_text("tools:\n  tiers:\n    enabled: false\n", encoding="utf-8")
    monkeypatch.setenv("CYT_CONFIG", str(config_path))
    code = tiers_main(["status"])
    assert code == 2
