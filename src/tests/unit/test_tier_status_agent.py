"""Tests for agent-scoped tier status skill filtering."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from _pytest.monkeypatch import MonkeyPatch

from cyt.tiers.cli import main as tiers_main
from cyt.tiers.config import resolve_tier_status_agent
from cyt.tiers.manager import _managers


@pytest.fixture(autouse=True)
def clear_tier_managers() -> Iterator[None]:
    _managers.clear()
    yield
    _managers.clear()


def _write_status_config(tmp_path: Path, db_path: Path) -> None:
    (tmp_path / "config.yaml").write_text(
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


def _write_workspace_mcp_config(tmp_path: Path, *, default_agent: str = "cursor") -> None:
    config_dir = tmp_path / ".agents" / "cyt" / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "mcp-config.yaml").write_text(
        f"default_agent: {default_agent}\nagents:\n  cursor: mcp/cursor.json\n",
        encoding="utf-8",
    )


def test_resolve_tier_status_agent_uses_workspace_mcp_config_default(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    _write_workspace_mcp_config(tmp_path, default_agent="cursor")
    assert resolve_tier_status_agent({}, workspace_root=tmp_path, explicit=None) == "cursor"


def test_tiers_status_defaults_to_mcp_config_agent_and_filters_claude_skills(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / ".git").mkdir()
    _write_workspace_mcp_config(tmp_path, default_agent="cursor")

    shared = tmp_path / ".agents" / "skills" / "explain-simply"
    shared.mkdir(parents=True)
    (shared / "SKILL.md").write_text(
        "---\nname: explain-simply\ndescription: shared\n---\n# Explain\n",
        encoding="utf-8",
    )
    claude_only = tmp_path / ".claude" / "skills" / "gitnexus-cli"
    claude_only.mkdir(parents=True)
    (claude_only / "SKILL.md").write_text(
        "---\nname: gitnexus-cli\ndescription: claude only\n---\n# GitNexus\n",
        encoding="utf-8",
    )

    db_path = tmp_path / "tier_state.db"
    _write_status_config(tmp_path, db_path)
    monkeypatch.chdir(tmp_path)

    code = tiers_main(["status", "--workspace", str(tmp_path), "--kind", "skills"])
    assert code == 0
    out = capsys.readouterr().out
    assert "agent: cursor" in out
    assert "explain-simply" in out
    assert "gitnexus-cli" not in out


def test_tiers_status_agent_flag_selects_claude_skills(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / ".git").mkdir()
    _write_workspace_mcp_config(tmp_path, default_agent="cursor")

    claude_only = tmp_path / ".claude" / "skills" / "gitnexus-cli"
    claude_only.mkdir(parents=True)
    (claude_only / "SKILL.md").write_text(
        "---\nname: gitnexus-cli\ndescription: claude only\n---\n# GitNexus\n",
        encoding="utf-8",
    )

    db_path = tmp_path / "tier_state.db"
    _write_status_config(tmp_path, db_path)
    monkeypatch.chdir(tmp_path)

    code = tiers_main(
        [
            "status",
            "--workspace",
            str(tmp_path),
            "--kind",
            "skills",
            "--agent",
            "claude",
        ],
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "agent: claude" in out
    assert "gitnexus-cli" in out


def test_tiers_status_json_includes_agent(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / ".git").mkdir()
    _write_workspace_mcp_config(tmp_path, default_agent="cursor")
    db_path = tmp_path / "tier_state.db"
    _write_status_config(tmp_path, db_path)
    monkeypatch.chdir(tmp_path)

    code = tiers_main(["status", "--workspace", str(tmp_path), "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["agent"] == "cursor"
