"""Tests for MCPC skills/resources SWR snapshot cache."""

from __future__ import annotations

from unittest.mock import patch

from cyt.mcpc.skills_cache import (
    McpcSkillsSnapshot,
    _fetch_session_resources,
    _fetch_session_skills,
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
    with (
        patch("cyt.mcpc.skills_cache.mcpc_available", return_value=True),
        patch("cyt.mcpc.skills_cache._fetch_skills_snapshot_from_cli", return_value=fetched),
    ):
        refresh_mcpc_skills_snapshot(_CONFIG)
    snapshot = get_mcpc_skills_snapshot(_CONFIG, blocking=False)
    assert snapshot is not None
    assert snapshot.own_skill is not None
    assert snapshot.updated_at > 0.0


def test_fetch_session_resources_skips_without_resources_capability() -> None:
    calls: list[list[str]] = []

    def fake_run_mcpc_json(_executable: str, args: list[str], **_kwargs: object) -> object | None:
        calls.append(list(args))
        return []

    with (
        patch("cyt.mcpc.skills_cache.session_supports_capability", return_value=False),
        patch("cyt.mcpc.skills_cache.run_mcpc_json", side_effect=fake_run_mcpc_json),
    ):
        assert _fetch_session_resources("mcpc", "@codebase-memory", allowed_mime_types=set()) == []
    assert calls == []


def test_fetch_session_skills_uses_optional_method_for_list() -> None:
    with patch("cyt.mcpc.skills_cache.run_mcpc_json", return_value=[]) as run:
        assert _fetch_session_skills("mcpc", "@fff") == []
    run.assert_called_once_with(
        "mcpc",
        ["@fff", "skills-list"],
        optional_method=True,
    )
