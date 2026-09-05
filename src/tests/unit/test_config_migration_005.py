#!/usr/bin/env python3
"""Tests for revision 005 — per-agent skills directories."""

from __future__ import annotations

from typing import Any

from cyt.migrations.versions import load_revision_modules


def _upgrade_005(cfg: dict[str, Any]) -> dict[str, Any]:
    module = next(
        m for m in load_revision_modules() if m.revision == "005_skills_agent_directories"
    )
    return module.upgrade(cfg, scope="global")


def test_moves_agent_directories_out_of_global_skills() -> None:
    cfg = {
        "skills": {
            "directories": [
                "~/.cursor/skills",
                "~/.claude/skills",
                "/shared/custom",
            ],
        },
    }
    out = _upgrade_005(cfg)
    assert out["skills"]["directories"] == ["/shared/custom"]
    assert out["agents"]["cursor"]["skills"]["directories"] == ["~/.cursor/skills"]
    assert out["agents"]["claude"]["skills"]["directories"] == ["~/.claude/skills"]


def test_uses_default_global_directory_when_only_agent_paths_present() -> None:
    cfg = {
        "skills": {
            "directories": ["~/.cursor/skills", ".cursor/skills"],
        },
    }
    out = _upgrade_005(cfg)
    assert out["skills"]["directories"] == ["~/.agents/skills"]
    assert out["agents"]["cursor"]["skills"]["directories"] == [
        "~/.cursor/skills",
        ".cursor/skills",
    ]
