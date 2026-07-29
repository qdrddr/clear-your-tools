"""Tests for cyt-client session JSONL persistence."""

from __future__ import annotations

import time
from pathlib import Path

from cyt_client.sessions import (
    append_session_log,
    cleanup_stale_session_logs,
    read_session_log_file,
    session_id_from_payload,
    session_log_path,
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


def test_session_log_path_resolves_under_agent_dir(tmp_path: Path) -> None:
    payload = {"cwd": str(tmp_path), "session_id": "sess-1", "cyt_agent": "claude"}
    path = session_log_path(payload)
    assert path == tmp_path / ".claude/cyt/sessions/sess-1.jsonl"
