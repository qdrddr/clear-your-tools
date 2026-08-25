"""Unit tests for cyt-client agent skill read interceptor."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from cyt_client.agent_interceptor import (
    SessionLogIndex,
    content_sha256_for_file,
    format_search_query,
    is_skill_md_under_directories,
    resolve_read_intercept_mode,
    skill_item_key_for_path,
)
from cyt_client.sessions import read_session_log_file


def test_resolve_read_intercept_mode_promotes_to_full_after_three_entries() -> None:
    key = "skill:~/.cursor/skills/demo/SKILL.md"
    entries = [
        {"kind": "skill", "key": key, "hash": "abc", "full": False},
        {"kind": "skill", "key": key, "hash": "abc", "full": False},
        {"kind": "skill", "key": key, "hash": "abc", "full": False},
    ]
    index = SessionLogIndex.from_entries(entries)
    assert resolve_read_intercept_mode(key=key, current_hash="abc", index=index) == "full"


def test_is_skill_md_under_directories(tmp_path: Path) -> None:
    skill_dir = tmp_path / ".cursor" / "skills"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "demo" / "SKILL.md"
    skill_path.parent.mkdir()
    skill_path.write_text("# Demo\n", encoding="utf-8")
    assert is_skill_md_under_directories(str(skill_path), [skill_dir.resolve()])


def test_handle_read_intercept_allows_outside_skill_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cyt_client.agent_interceptor import handle_read_intercept

    monkeypatch.setattr(
        "cyt_client.agent_interceptor.skills_hook_agent_interceptor_enabled",
        lambda: True,
    )
    other = tmp_path / "other.md"
    other.write_text("# x\n", encoding="utf-8")
    payload = {
        "hook_event_name": "preToolUse",
        "conversation_id": "test",
        "workspace_roots": [str(tmp_path)],
        "tool_name": "Read",
        "tool_input": {"path": str(other)},
    }

    def _fail_post(*_args: object, **_kwargs: object) -> tuple[int, bytes]:
        raise AssertionError("daemon should not be called")

    stdout = handle_read_intercept(payload, post_hook_inject=_fail_post)
    assert stdout is not None
    parsed = json.loads(stdout)
    assert parsed["permission"] == "allow"
    assert "updated_input" not in parsed


def test_format_search_query() -> None:
    assert format_search_query("hello", "world") == "User_Asks: hello; Assistant_Says: world"


def test_extract_read_tool_call_user_format() -> None:
    from cyt_client.agent_interceptor import extract_read_tool_call

    payload = {
        "hook_event_name": "preToolUse",
        "type": "tool_use",
        "name": "Read",
        "input": {"path": "/Users/me/.cursor/skills/x/SKILL.md"},
    }
    tool_name, tool_input = extract_read_tool_call(payload)
    assert tool_name == "Read"
    assert tool_input == {"path": "/Users/me/.cursor/skills/x/SKILL.md"}


def test_extract_read_tool_call_accepts_read_file_alias() -> None:
    from cyt_client.agent_interceptor import extract_read_tool_call

    payload = {
        "hook_event_name": "preToolUse",
        "tool_name": "ReadFile",
        "tool_input": {"path": "/Users/me/.cursor/skills/x/SKILL.md"},
    }
    tool_name, tool_input = extract_read_tool_call(payload)
    assert tool_name == "ReadFile"
    assert tool_input == {"path": "/Users/me/.cursor/skills/x/SKILL.md"}


def test_is_before_read_file_event_structural_fallback() -> None:
    from cyt_client.tool_gate import is_before_read_file_event

    payload = {
        "file_path": "/Users/me/.cursor/skills/RTK/SKILL.md",
        "content": "---\nname: RTK\n---\n",
    }
    assert is_before_read_file_event(payload) is True

    pre_tool_payload = {
        "hook_event_name": "preToolUse",
        "tool_name": "Read",
        "tool_input": {"path": "/Users/me/.cursor/skills/RTK/SKILL.md"},
    }
    assert is_before_read_file_event(pre_tool_payload) is False


def test_skill_entry_matches_read_path_across_home_and_absolute() -> None:
    from cyt_client.agent_interceptor import skill_entry_matches_read_path

    entry = {
        "kind": "skill",
        "key": "skill:~/.cursor/skills/RTK/SKILL.md",
        "path": "~/.cursor/skills/RTK/SKILL.md",
        "hash": "abc",
        "full": True,
    }
    home_path = Path("~/.cursor/skills/RTK/SKILL.md").expanduser()
    assert skill_entry_matches_read_path(entry, str(home_path))
    assert skill_entry_matches_read_path(entry, "~/.cursor/skills/RTK/SKILL.md")


def test_should_deny_when_session_key_uses_tilde_and_read_uses_absolute_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cyt_client.agent_interceptor import (
        SessionLogIndex,
        content_sha256_for_file,
        matching_skill_entry_after_latest_turn,
        should_deny_same_turn_preinjected_skill,
        skill_item_key_for_path,
    )

    skill_path = Path("~/.cursor/skills/RTK/SKILL.md").expanduser()
    if not skill_path.is_file():
        pytest.skip("RTK skill not installed")
    content_hash = content_sha256_for_file(skill_path)
    session_key = skill_item_key_for_path(skill_path)
    entries: list[dict[str, Any]] = [
        {"kind": "turn", "key": "turn:1", "prompt": "How do i use rtk?", "assistant": ""},
        {
            "kind": "skill",
            "key": session_key,
            "path": "~/.cursor/skills/RTK/SKILL.md",
            "hash": content_hash,
            "full": True,
        },
    ]
    index = SessionLogIndex.from_entries(entries)
    assert matching_skill_entry_after_latest_turn(str(skill_path), index) is not None
    assert should_deny_same_turn_preinjected_skill(
        session_key,
        index,
        session_prompt="How do i use rtk?",
        transcript_prompt="",
        content_hash=content_hash,
        read_path=str(skill_path),
    )


def test_should_deny_same_turn_preinjected_skill() -> None:
    from cyt_client.agent_interceptor import (
        SessionLogIndex,
        should_deny_same_turn_preinjected_skill,
    )

    key = "skill:~/.cursor/skills/demo/SKILL.md"
    entries: list[dict[str, Any]] = [
        {"kind": "turn", "key": "turn:1", "prompt": "use skill", "assistant": ""},
        {"kind": "skill", "key": key, "hash": "abc", "full": False},
    ]
    index = SessionLogIndex.from_entries(entries)
    assert should_deny_same_turn_preinjected_skill(
        key,
        index,
        session_prompt="use skill",
        transcript_prompt="<user_query>\nuse skill\n</user_query>",
        content_hash="abc",
    )
    assert not should_deny_same_turn_preinjected_skill(
        key,
        index,
        session_prompt="use skill",
        transcript_prompt="different prompt",
        content_hash="abc",
    )
    assert should_deny_same_turn_preinjected_skill(
        key,
        index,
        session_prompt="use skill",
        transcript_prompt="",
        content_hash="abc",
    )


def test_should_not_deny_when_skill_not_logged_after_latest_turn() -> None:
    from cyt_client.agent_interceptor import (
        SessionLogIndex,
        should_deny_same_turn_preinjected_skill,
    )

    key = "skill:~/.cursor/skills/demo/SKILL.md"
    entries: list[dict[str, Any]] = [
        {"kind": "turn", "key": "turn:1", "prompt": "first", "assistant": ""},
        {"kind": "skill", "key": key, "hash": "abc", "full": False},
        {"kind": "turn", "key": "turn:2", "prompt": "second", "assistant": ""},
    ]
    index = SessionLogIndex.from_entries(entries)
    assert not should_deny_same_turn_preinjected_skill(
        key,
        index,
        session_prompt="second",
        transcript_prompt="",
        content_hash="abc",
    )


def test_handle_before_read_file_denies_same_turn_preinjected_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cyt_client.agent_interceptor import (
        handle_before_read_file_intercept,
        skill_item_key_for_path,
    )

    monkeypatch.setattr(
        "cyt_client.agent_interceptor.skills_hook_agent_interceptor_enabled",
        lambda: True,
    )
    skill_path = tmp_path / ".cursor" / "skills" / "RTK" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# RTK\n", encoding="utf-8")
    skill_key = skill_item_key_for_path(skill_path)
    content_hash = content_sha256_for_file(skill_path)
    sessions_dir = tmp_path / ".cursor" / "cyt" / "sessions"
    sessions_dir.mkdir(parents=True)
    session_id = "606ac439-7892-4787-8f4f-14477946a564"
    log_path = sessions_dir / f"{session_id}.jsonl"
    log_path.write_text(
        "\n".join(
            [
                '{"type":"meta","agent":"cursor"}',
                json.dumps(
                    {
                        "kind": "skill_directories",
                        "key": "skill_directories",
                        "directories": [str(skill_path.parent.parent)],
                    },
                ),
                '{"kind":"turn","key":"turn:abc","prompt":"How to use rtk?","assistant":""}',
                json.dumps(
                    {
                        "kind": "skill",
                        "key": skill_key,
                        "hash": content_hash,
                        "full": False,
                    },
                ),
            ],
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CYT_LAUNCH_AGENT", "cursor")
    monkeypatch.setattr(
        "cyt_client.transcript.last_user_from_payload",
        lambda _payload: "<user_query>\nHow to use rtk?\n</user_query>",
    )
    payload = {
        "hook_event_name": "beforeReadFile",
        "conversation_id": session_id,
        "session_id": session_id,
        "file_path": str(skill_path),
        "workspace_roots": [str(tmp_path)],
    }

    stdout = handle_before_read_file_intercept(payload)
    parsed = json.loads(stdout)
    assert parsed["permission"] == "deny"
    assert "redundant" in parsed["user_message"].lower()


def test_read_intercept_does_not_duplicate_skill_log_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cyt_client.agent_interceptor import handle_read_intercept

    monkeypatch.setattr(
        "cyt_client.agent_interceptor.skills_hook_agent_interceptor_enabled",
        lambda: True,
    )
    skill_dir = tmp_path / ".cursor" / "skills" / "RTK"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text("# RTK\nAlways prefix shell commands with rtk.\n", encoding="utf-8")
    skill_key = skill_item_key_for_path(skill_path)
    content_hash = "c31d1d0dbc5c24ec21edc1ca7fbf8936ed6aff8ccfed68a99a8a4df5117fc8c2"  # pragma: allowlist secret
    sessions_dir = tmp_path / ".cursor" / "cyt" / "sessions"
    sessions_dir.mkdir(parents=True)
    session_id = "a7dbef47-cbb7-4bd9-a159-01ff30411324"
    log_path = sessions_dir / f"{session_id}.jsonl"
    log_path.write_text(
        "\n".join(
            [
                '{"type":"meta","agent":"cursor"}',
                json.dumps(
                    {
                        "kind": "skill_directories",
                        "key": "skill_directories",
                        "directories": [str(skill_dir.parent)],
                    },
                ),
                json.dumps(
                    {
                        "kind": "turn",
                        "key": "turn:abc",
                        "prompt": "How do i use rtk?",
                        "assistant": "",
                    },
                ),
                json.dumps(
                    {
                        "kind": "skill",
                        "key": skill_key,
                        "hash": content_hash,
                        "full": False,
                        "source": "file",
                        "body": "# RTK\nAlways prefix shell commands with rtk.",
                        "name": "RTK",
                        "path": str(skill_path),
                    },
                ),
            ],
        )
        + "\n",
        encoding="utf-8",
    )
    skinny = tmp_path / ".cyt" / "skinny" / session_id / "c31d1d0dabc1.md"
    skinny.parent.mkdir(parents=True, exist_ok=True)
    skinny.write_text("# RTK\nAlways prefix shell commands with rtk.", encoding="utf-8")
    daemon_response = json.dumps(
        {
            "agent_interceptor": True,
            "permission": "allow",
            "updated_input": {"path": str(skinny)},
            "skill_log_entry": {
                "kind": "skill",
                "key": skill_key,
                "hash": content_hash,
                "full": False,
                "source": "file",
                "body": "# RTK\nAlways prefix shell commands with rtk.",
                "name": "RTK",
                "path": str(skill_path),
            },
        },
    ).encode()
    payload = {
        "hook_event_name": "preToolUse",
        "conversation_id": session_id,
        "session_id": session_id,
        "workspace_roots": [str(tmp_path)],
        "cwd": str(tmp_path),
        "tool_name": "Read",
        "tool_input": {"path": str(skill_path)},
    }

    def _post(*_args: object, **_kwargs: object) -> tuple[int, bytes]:
        return 200, daemon_response

    stdout = handle_read_intercept(payload, post_hook_inject=_post)
    assert stdout is not None
    parsed = json.loads(stdout)
    assert parsed["permission"] == "allow"
    _agent, items = read_session_log_file(log_path)
    skill_items = [entry for entry in items if entry.get("kind") == "skill"]
    assert len(skill_items) == 1


def test_skill_item_key_for_path() -> None:
    key = skill_item_key_for_path("/Users/me/.cursor/skills/x/SKILL.md")
    assert key.startswith("skill:")
