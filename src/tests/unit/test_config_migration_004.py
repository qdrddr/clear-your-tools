#!/usr/bin/env python3
"""Tests for revision 004 — permissions layout normalization."""

from __future__ import annotations

from typing import Any

from cyt.migrations.versions import load_revision_modules


def _upgrade_004(cfg: dict[str, Any]) -> dict[str, Any]:
    module = next(
        m for m in load_revision_modules() if m.revision == "004_permissions_agents_layout"
    )
    return module.upgrade(cfg, scope="workspace")


def test_normalizes_path_deny_entries_to_string_form() -> None:
    cfg = {
        "agents": {
            "cursor": {
                "skills": {
                    "permissions": {
                        "deny": [{"path": ".codex/skills/.system"}, "upgrade-guide"],
                    },
                },
            },
        },
    }
    out = _upgrade_004(cfg)
    deny = out["agents"]["cursor"]["skills"]["permissions"]["deny"]
    assert "path:.codex/skills/.system" in deny
    assert "upgrade-guide" in deny
