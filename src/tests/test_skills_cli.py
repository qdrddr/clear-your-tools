"""Tests for `cyt skills` hook CLI."""

from __future__ import annotations

import json
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

from cyt.skills import cli as skills_cli


def _write_skill(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_session_start_registers_without_output(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache_path = str(root / "cache.db")
        payload = {
            "hook_event_name": "SessionStart",
            "session_id": "sess-abc",
            "model": "claude-sonnet-4",
        }
        monkeypatch.setattr("sys.stdin", StringIO(json.dumps(payload)))
        stdout = StringIO()
        monkeypatch.setattr("sys.stdout", stdout)

        config = {"skills": {"enabled": True, "cache": {"database": {"path": cache_path}}}}
        with patch("cyt.skills.cli.load_config", return_value=config):
            with patch("cyt.skills.cache.skills_cache_db_path", return_value=cache_path):
                skills_cli.run()

        assert stdout.getvalue() == ""

        from cyt.skills.cache import SessionCacheDB

        with patch("cyt.skills.cache.skills_cache_db_path", return_value=cache_path):
            db = SessionCacheDB.open(config)
            try:
                assert db.lookup_model("sess-abc") == "claude-sonnet-4"
            finally:
                db.close()


def test_user_prompt_emits_json_hook_output(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skills_dir = root / "skills"
        catalog_dir = root / "catalog"
        cache_path = str(root / "cache.db")
        _write_skill(
            skills_dir / "create-hook.md",
            "# Create Hook\n\nAgent hooks for Claude Code sessions.\n",
        )

        payload = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "sess-abc",
            "prompt": "configure agent hooks for sessions",
        }
        monkeypatch.setattr("sys.stdin", StringIO(json.dumps(payload)))
        stdout = StringIO()
        monkeypatch.setattr("sys.stdout", stdout)

        config = {
            "skills": {
                "enabled": True,
                "pipeline": "bm25",
                "catalog_dir": str(catalog_dir),
                "directories": [str(skills_dir)],
                "cache": {"database": {"path": cache_path}},
                "max_tokens_per_request": 4000,
                "pageindex": {"enable_bm25_chunking": True},
            },
            "pruning": {"bm25": {"score_skills": 0.0}},
            "stats": {"database": {"path": str(root / "stats.db")}},
        }

        with patch("cyt.skills.cli.load_config", return_value=config):
            with patch("cyt.skills.cache.skills_cache_db_path", return_value=cache_path):
                with patch("cyt.config.stats_db_path", return_value=str(root / "stats.db")):
                    from cyt.skills.cache import SessionCacheDB

                    with patch("cyt.skills.cache.skills_cache_db_path", return_value=cache_path):
                        db = SessionCacheDB.open(config)
                        try:
                            db.upsert_session("sess-abc", "claude-sonnet-4")
                        finally:
                            db.close()
                    skills_cli.run()

        output = json.loads(stdout.getvalue())
        assert output["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
        assert "<agent-skills>" in output["hookSpecificOutput"]["additionalContext"]


def test_disabled_config_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "hook_event_name": "UserPromptSubmit",
        "session_id": "sess-abc",
        "prompt": "hello",
    }
    monkeypatch.setattr("sys.stdin", StringIO(json.dumps(payload)))
    stdout = StringIO()
    monkeypatch.setattr("sys.stdout", stdout)

    with patch("cyt.skills.cli.load_config", return_value={"skills": {"enabled": False}}):
        skills_cli.run()

    assert stdout.getvalue() == ""
