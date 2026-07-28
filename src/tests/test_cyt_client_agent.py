"""Tests for cyt-client harness detection.

Gherkin equivalents: ``src/tests/gherkin/features/harness_detection.feature``.
"""

from __future__ import annotations

import json

import pytest

from cyt_client.agent import (
    CLAUDE_CODE_ENTRYPOINT_ENV,
    CLAUDE_PROJECT_DIR_ENV,
    CLAUDECODE_ENV,
    CODEX_HOME_ENV,
    CURSOR_VERSION_ENV,
    CYT_LAUNCH_AGENT_ENV,
    infer_harness_agent,
    looks_like_cursor_payload,
)
from cyt_client.cursor import is_cursor_hook_payload
from cyt_client.transcript import enrich_hook_payload


def _clear_harness_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        CODEX_HOME_ENV,
        CURSOR_VERSION_ENV,
        CLAUDE_PROJECT_DIR_ENV,
        CLAUDECODE_ENV,
        CLAUDE_CODE_ENTRYPOINT_ENV,
        CYT_LAUNCH_AGENT_ENV,
    ):
        monkeypatch.delenv(name, raising=False)


def test_cursor_version_env_beats_launch_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_harness_env(monkeypatch)
    monkeypatch.setenv(CURSOR_VERSION_ENV, "3.10.17")
    monkeypatch.setenv(CYT_LAUNCH_AGENT_ENV, "claude")
    assert infer_harness_agent({}) == "cursor"


def test_payload_cursor_version_beats_launch_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_harness_env(monkeypatch)
    monkeypatch.setenv(CYT_LAUNCH_AGENT_ENV, "claude")
    payload = {"cursor_version": "3.10.17", "hook_event_name": "UserPromptSubmit"}
    assert infer_harness_agent(payload) == "cursor"


def test_before_submit_prompt_beats_launch_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_harness_env(monkeypatch)
    monkeypatch.setenv(CYT_LAUNCH_AGENT_ENV, "claude")
    payload = {"hook_event_name": "beforeSubmitPrompt", "prompt": "hello"}
    assert infer_harness_agent(payload) == "cursor"


def test_session_start_does_not_infer_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_harness_env(monkeypatch)
    payload = {"hook_event_name": "sessionStart"}
    assert infer_harness_agent(payload) is None
    assert not looks_like_cursor_payload(payload)


def test_session_end_does_not_infer_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_harness_env(monkeypatch)
    payload = {"hook_event_name": "sessionEnd"}
    assert infer_harness_agent(payload) is None
    assert not looks_like_cursor_payload(payload)


def test_session_end_still_routes_cursor_cli() -> None:
    assert is_cursor_hook_payload({"hook_event_name": "sessionEnd"})


def test_session_start_still_routes_cursor_cli() -> None:
    from cyt_client.cursor import is_session_start_event

    assert is_cursor_hook_payload({"hook_event_name": "sessionStart"})
    assert is_session_start_event({"hook_event_name": "sessionStart"})


def test_workspace_roots_alone_does_not_infer_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_harness_env(monkeypatch)
    payload = {"workspace_roots": ["/tmp/project"], "hook_event_name": "UserPromptSubmit"}
    assert infer_harness_agent(payload) is None
    assert not looks_like_cursor_payload(payload)


def test_codex_home_without_launch_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_harness_env(monkeypatch)
    monkeypatch.setenv(CODEX_HOME_ENV, "/Users/me/.codex")
    assert infer_harness_agent({}) == "codex"


def test_claude_project_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_harness_env(monkeypatch)
    monkeypatch.setenv(CLAUDE_PROJECT_DIR_ENV, "/tmp/project")
    assert infer_harness_agent({}) == "claude"


def test_claudecode_backup(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_harness_env(monkeypatch)
    monkeypatch.setenv(CLAUDECODE_ENV, "1")
    assert infer_harness_agent({}) == "claude"


def test_transcript_path_last_resort(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_harness_env(monkeypatch)
    payload = {"transcript_path": "/Users/me/.claude/projects/foo/session.jsonl"}
    assert infer_harness_agent(payload) == "claude"


def test_transcript_path_nested_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_harness_env(monkeypatch)
    payload = {
        "payload": {"transcript_path": "/Users/me/.codex/sessions/2026/rollout.jsonl"},
    }
    assert infer_harness_agent(payload) == "codex"


def test_cursor_transcript_path_uses_projects_segment(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_harness_env(monkeypatch)
    payload = {
        "transcript_path": "/Users/me/.cursor/projects/foo/session.jsonl",
    }
    assert infer_harness_agent(payload) == "cursor"


def test_cursor_transcript_path_ignores_generic_cursor_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_harness_env(monkeypatch)
    payload = {"transcript_path": "/Users/me/.cursor/chats/session.jsonl"}
    assert infer_harness_agent(payload) is None


def test_launch_agent_last_resort(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_harness_env(monkeypatch)
    monkeypatch.setenv(CYT_LAUNCH_AGENT_ENV, "codex")
    assert infer_harness_agent({}) == "codex"


def test_codex_home_beats_cursor_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_harness_env(monkeypatch)
    monkeypatch.setenv(CODEX_HOME_ENV, "/Users/me/.codex")
    payload = {"cursor_version": "3.10.17"}
    assert infer_harness_agent(payload) == "codex"


def test_enrich_hook_payload_sets_cyt_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_harness_env(monkeypatch)
    monkeypatch.setenv(CYT_LAUNCH_AGENT_ENV, "claude")
    payload = {
        "hook_event_name": "beforeSubmitPrompt",
        "prompt": "hello",
        "conversation_id": "conv-1",
    }
    enriched = json.loads(enrich_hook_payload(json.dumps(payload).encode()))
    assert enriched["cyt_agent"] == "cursor"
