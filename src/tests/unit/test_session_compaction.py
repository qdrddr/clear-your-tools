"""Tests for preCompact session log persistence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cyt_client.session_compaction import (
    build_compaction_entry,
    is_pre_compact_event,
    persist_compaction_to_session_log,
)
from cyt_client.sessions import read_session_log_file


def test_is_pre_compact_event() -> None:
    assert is_pre_compact_event({"hook_event_name": "preCompact"})
    assert is_pre_compact_event({"hookEventName": "PreCompact"})


def test_build_compaction_entry_marks_first_compaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    payload = {
        "hook_event_name": "preCompact",
        "session_id": "sess-1",
        "cyt_agent": "claude",
        "trigger": "auto",
        "context_usage_percent": 85,
    }
    entry = build_compaction_entry(payload)
    assert entry["kind"] == "compaction"
    assert entry["key"] == "compaction"
    assert entry["payload"]["trigger"] == "auto"
    assert entry["payload"]["is_first_compaction"] is True


def test_persist_compaction_from_pre_compact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude" / "cyt" / "sessions").mkdir(parents=True)
    payload = {
        "hook_event_name": "preCompact",
        "session_id": "sess-1",
        "cyt_agent": "claude",
        "trigger": "manual",
        "status": "complete",
    }
    assert persist_compaction_to_session_log(payload) is True
    log_path = tmp_path / ".claude/cyt/sessions/sess-1.jsonl"
    _agent, entries = read_session_log_file(log_path)
    kinds = [entry.get("kind") for entry in entries]
    assert kinds == ["compaction"]


def test_idempotent_pre_compact_compaction_appends(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    payload = {
        "hook_event_name": "preCompact",
        "session_id": "sess-2",
        "cyt_agent": "codex",
    }
    assert persist_compaction_to_session_log(payload) is True
    assert persist_compaction_to_session_log(payload) is True
    log_path = tmp_path / ".codex/cyt/sessions/sess-2.jsonl"
    lines = [line for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) >= 2
    assert all(json.loads(line)["kind"] == "compaction" for line in lines if "compaction" in line)
