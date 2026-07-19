"""Tests for MCPC skills/resources SWR snapshot cache."""

from __future__ import annotations

from unittest.mock import patch

from cyt.mcpc.skills_cache import (
    McpcSkillsSnapshot,
    clear_mcpc_skills_cache,
    get_mcpc_skills_snapshot,
    refresh_mcpc_skills_snapshot,
)

_CONFIG = {
    "pruning": {
        "inject_via": "hook",
        "tools": {
            "enabled": True,
            "hook": {
                "tools_from": "mcpc",
                "mcpc": {"executable": "mcpc"},
            },
        },
    },
}


def setup_function() -> None:
    clear_mcpc_skills_cache()


def test_get_mcpc_skills_snapshot_returns_empty_before_refresh() -> None:
    with patch("cyt.mcpc.cache_scheduler.start_mcpc_cache_scheduler"):
        snapshot = get_mcpc_skills_snapshot(_CONFIG, blocking=False)
    assert snapshot is not None
    assert snapshot.own_skill is None
    assert snapshot.in_session == []
    assert snapshot.resources == []
    assert snapshot.updated_at == 0.0


def test_refresh_mcpc_skills_snapshot_updates_memory() -> None:
    fetched = McpcSkillsSnapshot(
        own_skill={
            "path": "mcpc/help/SKILL.md",
            "content": "---\nname: mcpc\n---\n\nBody\n",
            "content_sha256": "abc",
        },
        in_session=[],
        resources=[],
        updated_at=1.0,
    )
    with patch("cyt.mcpc.skills_cache._fetch_skills_snapshot_from_cli", return_value=fetched):
        refresh_mcpc_skills_snapshot(_CONFIG)
    snapshot = get_mcpc_skills_snapshot(_CONFIG, blocking=False)
    assert snapshot is not None
    assert snapshot.own_skill is not None
    assert snapshot.updated_at > 0.0
