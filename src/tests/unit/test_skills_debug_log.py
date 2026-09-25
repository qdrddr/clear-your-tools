"""Tests for hook debug log files."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from cyt.skills.debug_log import (
    extract_hook_payload,
    payload_mutations,
    split_hook_and_cyt_client,
    write_hook_debug_log,
)


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


def test_extract_hook_payload_prefers_cyt_hook_payload() -> None:
    original = {"hook_event_name": "UserPromptSubmit", "prompt": "hello"}
    request = {
        **original,
        "cyt_hook_payload": original,
        "cyt_agent": "claude",
    }
    assert extract_hook_payload(request) == original


def test_payload_mutations_reports_server_changes() -> None:
    request = {"hook_event_name": "beforeSubmitPrompt", "prompt": "hi"}
    server = {"hook_event_name": "UserPromptSubmit", "prompt": "hi", "cwd": "/tmp"}
    mutations = payload_mutations(request, server)
    assert {"field": "cwd", "change": "added", "value": "/tmp"} in mutations
    assert {
        "field": "hook_event_name",
        "change": "updated",
        "from": "beforeSubmitPrompt",
        "to": "UserPromptSubmit",
    } in mutations


def test_normal_mode_does_not_write_hook_debug_logs(tmp_path: Path) -> None:
    from cyt.config import load_config
    from cyt.hook.daemon import HookDaemonStartResult
    from cyt.skills.cli import run_hook_payload
    from cyt.skills.debug_log import _FALLBACK_DEBUG_DIR

    payload = {
        "hook_event_name": "SessionStart",
        "session_id": "sess-normal-no-debug",
        "cwd": str(tmp_path),
    }
    with patch("cyt.hook.daemon.daemon_start") as daemon_start:
        daemon_start.return_value = HookDaemonStartResult(
            outcome="reused",
            port=8834,
            hook_url="http://127.0.0.1:8834/hook/inject",
            pid=None,
            reused=True,
        )
        run_hook_payload(payload, load_config(), debug=False)

    assert not (tmp_path / ".debug" / "hooks").exists()
    assert not _FALLBACK_DEBUG_DIR.exists()
    assert list(_FALLBACK_DEBUG_DIR.glob("*.json")) == []


def test_write_hook_debug_log_uses_hooks_dir_and_full_payload(tmp_path: Path) -> None:
    hook = {
        "hook_event_name": "UserPromptSubmit",
        "session_id": "sess-1",
        "prompt": "hello",
        "cwd": str(tmp_path),
    }
    request_payload = {
        **hook,
        "cyt_hook_payload": hook,
        "cyt_agent": "claude",
        "cyt_skills": [{"path": "/tmp/SKILL.md", "content": "skill"}],
    }
    server_payload = dict(request_payload)
    server_payload["session_id"] = "sess-1-normalized"

    path = write_hook_debug_log(
        request_payload=request_payload,
        server_payload=server_payload,
        cwd=str(tmp_path),
        skills_enabled=True,
        tools_enabled=True,
        outcome="user_prompt_injected",
        details={"stdout": {"additional_context_len": 42}},
    )

    import cyt.skills.debug_log as debug_log_module

    assert path.parent == debug_log_module.hooks_debug_dirs(str(tmp_path))[0]
    assert path.parent.name == "hooks"
    entry = json.loads(path.read_text(encoding="utf-8"))
    assert entry["stdin_raw"] == hook
    assert entry["cyt_client"]["payload"] == request_payload
    assert entry["cyt_client"]["agent"] == "claude"
    assert entry["cyt_client"]["injection"] == {"stdout": {"additional_context_len": 42}}
    assert entry["server"]["payload"]["session_id"] == "sess-1-normalized"
    assert any(m["field"] == "session_id" for m in entry["server"]["mutations"])
