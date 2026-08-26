"""Tests for cyt-client session JSONL persistence."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from cyt_client.sessions import (
    append_resource_entries,
    append_session_log,
    append_skill_entries,
    append_tool_entries,
    cleanup_stale_session_logs,
    entries_after_latest_compaction,
    index_of_latest_compaction,
    read_session_log_file,
    read_tool_catalog_hashes,
    session_id_from_payload,
    session_log_path,
    sessions_dir_for_agent,
    sessions_dir_for_payload,
)


def test_session_id_from_payload_prefers_session_id() -> None:
    payload = {"session_id": "abc", "conversation_id": "def"}
    assert session_id_from_payload(payload) == "abc"


def test_session_id_from_payload_uses_conversation_id() -> None:
    payload = {"conversation_id": "cursor-conv"}
    assert session_id_from_payload(payload) == "cursor-conv"


def test_sessions_dir_for_payload_uses_workspace(tmp_path: Path) -> None:
    payload = {"cwd": str(tmp_path), "cyt_agent": "cursor"}
    assert sessions_dir_for_payload(payload) == tmp_path / ".cursor/cyt/sessions"


def test_sessions_dir_for_payload_uses_windows_workspace_roots(tmp_path: Path) -> None:
    payload = {
        "workspace_roots": [str(tmp_path)],
        "cyt_agent": "cursor",
        "conversation_id": "win-session",
    }
    assert sessions_dir_for_payload(payload) == tmp_path / ".cursor/cyt/sessions"


def test_session_log_path_windows_workspace(tmp_path: Path) -> None:
    payload = {
        "workspace_roots": [str(tmp_path)],
        "conversation_id": "abc-123",
        "cyt_agent": "cursor",
    }
    path = session_log_path(payload)
    assert path == tmp_path / ".cursor/cyt/sessions/abc-123.jsonl"


def test_append_and_read_session_log_with_meta(tmp_path: Path) -> None:
    path = tmp_path / "sess.jsonl"
    entries = [{"kind": "tool", "key": "tool:Shell", "hash": "abc", "full": False, "name": "Shell"}]
    append_session_log(path, entries, agent="cursor")
    agent, items = read_session_log_file(path)
    assert agent == "cursor"
    assert len(items) == 1
    assert items[0]["name"] == "Shell"


def test_cleanup_stale_session_logs_by_mtime(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    current = sessions_dir / "current.jsonl"
    stale = sessions_dir / "old.jsonl"
    current.write_text('{"kind":"tool"}\n', encoding="utf-8")
    stale.write_text('{"kind":"tool"}\n', encoding="utf-8")
    old_time = time.time() - 90000
    import os

    os.utime(stale, (old_time, old_time))
    removed = cleanup_stale_session_logs(sessions_dir, "current", max_age_seconds=86400)
    assert stale in removed
    assert current.exists()
    assert not stale.exists()


def test_session_log_path_resolves_under_agent_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / ".config" / "cyt"
    config_dir.mkdir(parents=True)
    (config_dir / "config.yaml").write_text(
        "pruning:\n  inject_via:\n    claude: hook\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    payload = {"cwd": str(tmp_path), "session_id": "sess-1", "cyt_agent": "claude"}
    path = session_log_path(payload)
    assert path == tmp_path / ".claude/cyt/sessions/sess-1.jsonl"


def test_entries_after_latest_compaction() -> None:
    entries = [
        {"kind": "tool", "key": "tool:a"},
        {"kind": "compaction", "key": "compaction"},
        {"kind": "tool", "key": "tool:b"},
    ]
    assert index_of_latest_compaction(entries) == 1
    assert len(entries_after_latest_compaction(entries)) == 1
    assert entries_after_latest_compaction(entries)[0]["key"] == "tool:b"


def test_read_tool_catalog_hashes_post_compaction_only(tmp_path: Path) -> None:
    path = tmp_path / "sess.jsonl"
    append_session_log(
        path,
        [
            {
                "kind": "tool_catalog",
                "key": "tool_catalog:cyt_mcp",
                "catalog": "cyt_mcp",
                "hash": "old-hash",
                "tools": [{"name": "old_tool", "input_schema": {}}],
            },
            {"kind": "compaction", "key": "compaction", "payload": {}},
            {
                "kind": "tool_catalog",
                "key": "tool_catalog:cyt_mcp",
                "catalog": "cyt_mcp",
                "hash": "new-hash",
                "tools": [{"name": "new_tool", "input_schema": {}}],
            },
        ],
        agent="claude",
    )
    hashes = read_tool_catalog_hashes(path)
    assert hashes["tool_catalog:cyt_mcp"] == "new-hash"


def test_append_skill_entries_dedupes_by_key_and_hash(tmp_path: Path) -> None:
    path = tmp_path / "sess.jsonl"
    skill = {
        "kind": "skill",
        "key": "skill:~/.cursor/skills/RTK/SKILL.md",
        "hash": "c31d1d0d",
        "full": False,
        "name": "RTK",
    }
    append_skill_entries(path, [skill], agent="cursor")
    append_skill_entries(path, [dict(skill, body="duplicate")], agent="cursor")
    _agent, items = read_session_log_file(path)
    skill_items = [entry for entry in items if entry.get("kind") == "skill"]
    assert len(skill_items) == 1


def test_append_resource_entries_dedupes_by_key_and_hash(tmp_path: Path) -> None:
    path = tmp_path / "sess.jsonl"
    resource = {
        "kind": "resource",
        "key": "resource:demo",
        "hash": "abc123",
        "full": False,
    }
    append_resource_entries(path, [resource], agent="cursor")
    append_resource_entries(path, [dict(resource, body="duplicate")], agent="cursor")
    _agent, items = read_session_log_file(path)
    resource_items = [entry for entry in items if entry.get("kind") == "resource"]
    assert len(resource_items) == 1


def test_append_tool_entries_dedupes_by_key_and_hash(tmp_path: Path) -> None:
    path = tmp_path / "sess.jsonl"
    tool = {"kind": "tool", "key": "tool:Shell", "hash": "abc", "full": False, "name": "Shell"}
    append_tool_entries(path, [tool], agent="cursor")
    append_tool_entries(path, [dict(tool, name="duplicate")], agent="cursor")
    _agent, items = read_session_log_file(path)
    tool_items = [entry for entry in items if entry.get("kind") == "tool"]
    assert len(tool_items) == 1


def test_sessions_dir_for_agent_home_when_inject_via_proxy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / ".config" / "cyt"
    config_dir.mkdir(parents=True)
    (config_dir / "config.yaml").write_text(
        "pruning:\n  inject_via:\n    claude: proxy\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    payload = {"cwd": str(tmp_path / "project"), "cyt_agent": "claude"}
    (tmp_path / "project").mkdir()
    assert sessions_dir_for_payload(payload) == sessions_dir_for_agent("claude")
