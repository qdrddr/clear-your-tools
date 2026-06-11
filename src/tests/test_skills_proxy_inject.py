"""Tests for proxy-side skills injection."""

from __future__ import annotations

import json
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

from cyt.skills import cli as skills_cli
from cyt.skills.proxy_inject import (
    anthropic_append_skills_to_system_messages,
    anthropic_append_text_to_system_content,
    inject_skills_into_anthropic_body,
    inject_skills_into_openai_body,
    openai_insert_skills_developer_message,
    openai_make_developer_message,
)


def _write_skill(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _skills_config(root: Path) -> dict:
    skills_dir = root / "skills"
    catalog_dir = root / "catalog"
    _write_skill(
        skills_dir / "create-hook.md",
        "---\nname: create-hook\ndescription: Agent hooks for Claude Code sessions.\n---\n"
        "# Create Hook\n\nAgent hooks for Claude Code sessions.\n",
    )
    return {
        "skills": {
            "enabled": True,
            "inject_via": "proxy",
            "pipeline": "bm25",
            "catalog_dir": str(catalog_dir),
            "directories": [str(skills_dir)],
            "max_tokens_per_request": 4000,
            "pageindex": {"enable_bm25_chunking": True},
        },
        "pruning": {"bm25": {"score_skills": 0.0}},
    }


def test_anthropic_append_text_to_system_content() -> None:
    message = {"role": "system", "content": "MCP instructions"}
    anthropic_append_text_to_system_content(message, "skills block")
    assert message["content"] == "MCP instructions\n\nskills block"


def test_anthropic_append_skills_to_system_messages_inserts_when_missing() -> None:
    updated = anthropic_append_skills_to_system_messages([], "skills text")
    assert len(updated) == 1
    assert updated[0]["role"] == "system"
    assert updated[0]["content"] == "skills text"


def test_openai_insert_skills_developer_message_before_last_user() -> None:
    input_items = [
        openai_make_developer_message("existing"),
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "first"}],
        },
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "last"}],
        },
    ]
    updated = openai_insert_skills_developer_message(input_items, "injected")
    assert updated[2]["role"] == "developer"
    assert "injected" in updated[2]["content"][0]["text"]
    assert updated[3]["content"][0]["text"] == "last"


def test_inject_skills_into_anthropic_body_appends_to_system() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _skills_config(root)
        body = {
            "model": "claude-test",
            "messages": [
                {"role": "system", "content": "# MCP Server Instructions"},
                {"role": "user", "content": "configure agent hooks for sessions"},
            ],
        }
        out, meta = inject_skills_into_anthropic_body(body, config)
        assert meta.skills_in > 0
        assert meta.query == "User_Asks: configure agent hooks for sessions"
        system = out["messages"][0]
        assert system["role"] == "system"
        assert "# MCP Server Instructions" in system["content"]
        assert "<agent-skills>" in system["content"]


def test_inject_skills_into_openai_body_inserts_developer_message() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _skills_config(root)
        body = {
            "model": "gpt-test",
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "configure agent hooks"}],
                },
            ],
        }
        out, meta = inject_skills_into_openai_body(body, config)
        assert meta.skills_in > 0
        developer_items = [
            item
            for item in out["input"]
            if isinstance(item, dict) and item.get("role") == "developer"
        ]
        assert len(developer_items) == 1
        assert "<agent-skills>" in developer_items[0]["content"][0]["text"]


def test_inject_skills_skipped_when_pipeline_rerank() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _skills_config(root)
        config["skills"]["pipeline"] = "rerank"
        body = {
            "messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "configure agent hooks for sessions"},
            ],
        }
        out, meta = inject_skills_into_anthropic_body(body, config)
        assert meta.skills_in == 0
        assert out["messages"][0]["content"] == "sys"


def test_inject_skills_skipped_when_pipeline_llm() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _skills_config(root)
        config["skills"]["pipeline"] = "llm"
        body = {
            "messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "configure agent hooks for sessions"},
            ],
        }
        out, meta = inject_skills_into_anthropic_body(body, config)
        assert meta.skills_in == 0
        assert out["messages"][0]["content"] == "sys"


def test_inject_skills_skipped_when_inject_via_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _skills_config(root)
        config["skills"]["inject_via"] = "hook"
        body = {
            "messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "configure agent hooks for sessions"},
            ],
        }
        out, meta = inject_skills_into_anthropic_body(body, config)
        assert meta.skills_in == 0
        assert out["messages"][0]["content"] == "sys"


def test_hook_skips_user_prompt_when_inject_via_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _skills_config(root)
        payload = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "sess-proxy",
            "prompt": "configure agent hooks for sessions",
        }
        monkeypatch.setattr("sys.stdin", StringIO(json.dumps(payload)))
        stdout = StringIO()
        monkeypatch.setattr("sys.stdout", stdout)

        with patch("cyt.skills.cli.load_config", return_value=config):
            skills_cli.run()

        assert stdout.getvalue() == ""
