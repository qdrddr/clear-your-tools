"""Unit tests for cyt-client agent skill read interceptor."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cyt_client.agent_interceptor import (
    SessionLogIndex,
    format_search_query,
    is_skill_md_under_directories,
    resolve_read_intercept_mode,
    skill_item_key_for_path,
)


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


def test_skill_item_key_for_path() -> None:
    key = skill_item_key_for_path("/Users/me/.cursor/skills/x/SKILL.md")
    assert key.startswith("skill:")
