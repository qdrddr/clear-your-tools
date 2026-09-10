"""Tests for cyt tiers CLI."""

from __future__ import annotations

from pathlib import Path

from cyt.tiers.cli import main as tiers_main


def test_tiers_status_json(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("USER", "cli-test-user")
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
    code = tiers_main(["status", "--json"])
    assert code == 0
