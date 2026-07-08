"""Tests for skills hook debug log files."""

from __future__ import annotations

import json
from pathlib import Path

from cyt.skills.debug_log import split_hook_and_cyt_client, write_skills_hook_debug_log


def test_split_hook_and_cyt_client_moves_cyt_prefixed_fields() -> None:
    payload = {
        "hook_event_name": "UserPromptSubmit",
        "prompt": "hello",
        "cyt_agent": "claude",
        "cyt_skills": [{"path": "/tmp/SKILL.md", "content": "skill"}],
        "cyt_transcript": [{"role": "user"}],
    }
    hook, cyt_client = split_hook_and_cyt_client(payload)
    assert hook == {"hook_event_name": "UserPromptSubmit", "prompt": "hello"}
    assert cyt_client["agent"] == "claude"
    assert cyt_client["skills"] == [{"path": "/tmp/SKILL.md", "content": "skill"}]
    assert cyt_client["transcript"] == [{"role": "user"}]


def test_write_skills_hook_debug_log_separates_hook_and_cyt_client(tmp_path: Path) -> None:
    hook = {
        "hook_event_name": "UserPromptSubmit",
        "session_id": "sess-1",
        "prompt": "hello",
        "cwd": str(tmp_path),
    }
    enriched = {
        **hook,
        "cyt_agent": "claude",
        "cyt_skills": [{"path": "/tmp/SKILL.md", "content": "skill"}],
    }
    raw_stdin = json.dumps(enriched)

    path = write_skills_hook_debug_log(
        raw_stdin=raw_stdin,
        payload=enriched,
        cwd=str(tmp_path),
        skills_enabled=True,
        tools_enabled=True,
        outcome="user_prompt_injected",
        details={"stdout": {"additional_context_len": 42}},
    )

    entry = json.loads(path.read_text(encoding="utf-8"))
    assert entry["stdin_raw"] == hook
    assert "payload" not in entry
    assert "details" not in entry
    assert entry["cyt_client"]["agent"] == "claude"
    assert entry["cyt_client"]["skills"] == [{"path": "/tmp/SKILL.md", "content": "skill"}]
    assert entry["cyt_client"]["skills_enabled"] is True
    assert entry["cyt_client"]["tools_enabled"] is True
    assert entry["cyt_client"]["outcome"] == "user_prompt_injected"
    assert entry["cyt_client"]["injection"] == {"stdout": {"additional_context_len": 42}}
