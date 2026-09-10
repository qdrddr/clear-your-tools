"""Migration revision 007 — config layout restructure."""

from __future__ import annotations

from typing import Any

from cyt.migrations.versions import load_revision_modules


def _upgrade_007(cfg: dict[str, Any]) -> dict[str, Any]:
    module = next(
        m for m in load_revision_modules() if m.revision == "007_config_layout_restructure"
    )
    return module.upgrade(cfg, scope="user")


def test_upgrade_moves_tools_and_agent_inject_via() -> None:
    cfg = {
        "mcp": {"permissions": {"deny": ["x"], "allow": []}},
        "hallucination_gate": {"enabled": True},
        "pruning": {
            "inject_via_default": "hook",
            "inject_via": {"cursor": "hook", "claude": "proxy", "codex": "proxy"},
            "max_batch_workers": 9,
            "tools": {
                "enabled": True,
                "hook": {"cyt_mcp": {"agent": "cursor", "executable": "cyt-mcp"}},
            },
        },
        "skills": {"hook": {"cursor_rule_file": {"enabled": False}}},
        "agents": {"cursor": {"mcp": {"permissions": {"deny": [], "allow": []}}}},
    }

    out = _upgrade_007(cfg)

    assert out["tools"]["enabled"] is True
    assert out["tools"]["max_batch_workers"] == 9
    assert out["tools"]["permissions"]["deny"] == ["x"]
    assert out["defaults"]["inject_via_default"] == "hook"
    assert out["defaults"]["hallucination_gate"]["enabled"] is True
    assert out["defaults"]["cyt_mcp_agent"] == "cursor"
    assert out["agents"]["cursor"]["tools"]["inject_via"] == "hook"
    assert out["agents"]["claude"]["tools"]["inject_via"] == "proxy"
    assert out["agents"]["cursor"]["tools"]["permissions"]["deny"] == []
    assert out["agents"]["cursor"]["hook"]["cursor_rule_file"]["enabled"] is False
    assert "mcp" not in out["agents"]["cursor"]
    assert "agent" not in out["tools"]["hook"]["cyt_mcp"]
    assert "pruning" not in out
    assert "mcp" not in out
    assert "hallucination_gate" not in out


def test_upgrade_preserves_existing_tools_block() -> None:
    cfg = {
        "tools": {"enabled": False},
        "pruning": {"tools": {"enabled": True, "sequence": ["bm25"]}},
    }
    out = _upgrade_007(cfg)
    assert out["tools"]["enabled"] is False
    assert "sequence" not in out["tools"]
