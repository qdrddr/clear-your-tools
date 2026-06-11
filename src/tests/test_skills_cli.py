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


def test_anthropic_session_start_then_user_prompt_resolves_model_from_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Claude Code: SessionStart registers model; UserPromptSubmit has no model field."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skills_dir = root / "skills"
        catalog_dir = root / "catalog"
        cache_path = str(root / "cache.db")
        session_id = "11b09b4b-f335-4a08-b618-8f607f6d7a46"
        _write_skill(
            skills_dir / "create-hook.md",
            "---\nname: create-hook\ndescription: Agent hooks for Claude Code sessions.\n---\n"
            "# Create Hook\n\nAgent hooks for Claude Code sessions.\n",
        )

        session_payload = {
            "session_id": session_id,
            "transcript_path": "/tmp/transcript.jsonl",
            "cwd": str(root),
            "hook_event_name": "SessionStart",
            "source": "startup",
            "model": "google/gemini-3-flash-preview",
        }
        monkeypatch.setattr("sys.stdin", StringIO(json.dumps(session_payload)))
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
                skills_cli.run()

        assert stdout.getvalue() == ""

        prompt_payload = {
            "session_id": session_id,
            "transcript_path": "/tmp/transcript.jsonl",
            "cwd": str(root),
            "permission_mode": "default",
            "hook_event_name": "UserPromptSubmit",
            "prompt": "configure agent hooks for sessions",
        }
        monkeypatch.setattr("sys.stdin", StringIO(json.dumps(prompt_payload)))
        stdout = StringIO()
        monkeypatch.setattr("sys.stdout", stdout)

        with patch("cyt.skills.cli.load_config", return_value=config):
            with patch("cyt.skills.cache.skills_cache_db_path", return_value=cache_path):
                with patch("cyt.config.stats_db_path", return_value=str(root / "stats.db")):
                    skills_cli.run()

        output = json.loads(stdout.getvalue())
        assert output["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
        assert "<agent-skills>" in output["hookSpecificOutput"]["additionalContext"]

        from cyt.skills.cache import SessionCacheDB

        with patch("cyt.skills.cache.skills_cache_db_path", return_value=cache_path):
            db = SessionCacheDB.open(config)
            try:
                assert db.lookup_model(session_id) == "google/gemini-3-flash-preview"
            finally:
                db.close()


def test_nested_payload_user_prompt_submit(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skills_dir = root / "skills"
        catalog_dir = root / "catalog"
        cache_path = str(root / "cache.db")
        _write_skill(
            skills_dir / "create-hook.md",
            "---\nname: create-hook\ndescription: Agent hooks for Claude Code sessions.\n---\n"
            "# Create Hook\n\nAgent hooks for Claude Code sessions.\n",
        )

        payload = {
            "hook_event_name": "UserPromptSubmit",
            "payload": {
                "session_id": "sess-nested",
                "prompt": "configure agent hooks for sessions",
            },
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
        }

        with patch("cyt.skills.cli.load_config", return_value=config):
            with patch("cyt.skills.cache.skills_cache_db_path", return_value=cache_path):
                from cyt.skills.cache import SessionCacheDB

                with patch("cyt.skills.cache.skills_cache_db_path", return_value=cache_path):
                    db = SessionCacheDB.open(config)
                    try:
                        db.upsert_session("sess-nested", "claude-sonnet-4")
                    finally:
                        db.close()
                skills_cli.run()

        output = json.loads(stdout.getvalue())
        assert output["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
        assert "<agent-skills>" in output["hookSpecificOutput"]["additionalContext"]


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
            "---\nname: create-hook\ndescription: Agent hooks for Claude Code sessions.\n---\n"
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


def test_codex_user_prompt_uses_payload_model_without_session_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skills_dir = root / "skills"
        catalog_dir = root / "catalog"
        cache_path = str(root / "cache.db")
        _write_skill(
            skills_dir / "create-hook.md",
            "---\nname: create-hook\ndescription: Agent hooks for Claude Code sessions.\n---\n"
            "# Create Hook\n\nAgent hooks for Claude Code sessions.\n",
        )

        payload = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "codex-sess",
            "model": "gpt-5.4-mini",
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
                    skills_cli.run()

        from cyt.skills.cache import SessionCacheDB

        with patch("cyt.skills.cache.skills_cache_db_path", return_value=cache_path):
            db = SessionCacheDB.open(config)
            try:
                assert db.lookup_model("codex-sess") == "gpt-5.4-mini"
            finally:
                db.close()

        output = json.loads(stdout.getvalue())
        assert output["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
        assert "<agent-skills>" in output["hookSpecificOutput"]["additionalContext"]


def test_cli_prompt_prints_injection_text(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skills_dir = root / "skills"
        catalog_dir = root / "catalog"
        cache_path = str(root / "cache.db")
        _write_skill(
            skills_dir / "context7.md",
            "---\nname: context7\ndescription: Use Context7 MCP for up-to-date library documentation.\n---\n"
            "# Context7\n\nUse Context7 MCP for up-to-date library documentation.\n",
        )

        stdout = StringIO()
        monkeypatch.setattr("sys.stdout", stdout)
        monkeypatch.chdir(root)

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
                skills_cli.run(prompt="use context7 library docs")

        output = stdout.getvalue()
        assert "<agent-skills>" in output
        assert "context7" in output
        assert "hookSpecificOutput" not in output


def test_debug_logs_stdin_when_skills_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache_path = str(root / "cache.db")
        payload = {
            "hook_event_name": "SessionStart",
            "session_id": "sess-debug",
            "model": "gpt-4",
            "cwd": str(root),
        }
        monkeypatch.setattr("sys.stdin", StringIO(json.dumps(payload)))
        stdout = StringIO()
        monkeypatch.setattr("sys.stdout", stdout)

        config = {
            "skills": {
                "enabled": False,
                "cache": {"database": {"path": cache_path}},
            },
        }
        with patch("cyt.skills.cli.load_config", return_value=config):
            with patch("cyt.skills.cache.skills_cache_db_path", return_value=cache_path):
                skills_cli.run(debug=True)

        debug_dir = root / ".debug" / "skills"
        logs = list(debug_dir.glob("*.json"))
        assert len(logs) == 1
        logged = json.loads(logs[0].read_text(encoding="utf-8"))
        assert logged["outcome"] == "skipped_disabled"
        assert logged["stdin_raw"] == json.dumps(payload)
        assert logged["payload"] == payload


def test_cli_prompt_runs_when_skills_disabled_in_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skills_dir = root / "skills"
        catalog_dir = root / "catalog"
        cache_path = str(root / "cache.db")
        _write_skill(
            skills_dir / "context7.md",
            "---\nname: context7\ndescription: Use Context7 MCP for up-to-date library documentation.\n---\n"
            "# Context7\n\nUse Context7 MCP for up-to-date library documentation.\n",
        )

        stdout = StringIO()
        monkeypatch.setattr("sys.stdout", stdout)
        monkeypatch.chdir(root)

        config = {
            "skills": {
                "enabled": False,
                "pipeline": "bm25",
                "catalog_dir": str(catalog_dir),
                "directories": [str(skills_dir)],
                "cache": {"database": {"path": cache_path}},
                "max_tokens_per_request": 4000,
                "pageindex": {"enable_bm25_chunking": True},
            },
            "pruning": {"bm25": {"score_skills": 0.0}},
        }

        with patch("cyt.skills.cli.load_config", return_value=config):
            with patch("cyt.skills.cache.skills_cache_db_path", return_value=cache_path):
                skills_cli.run(prompt="use context7 docs")

        assert "<agent-skills>" in stdout.getvalue()


def test_user_prompt_uses_transcript_query_before_search_skills(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hook enriches search query with last assistant turn from transcript_path."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skills_dir = root / "skills"
        catalog_dir = root / "catalog"
        cache_path = str(root / "cache.db")
        transcript = root / "session.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "prior assistant reply"}],
                    },
                },
            ),
            encoding="utf-8",
        )
        _write_skill(
            skills_dir / "create-hook.md",
            "---\nname: create-hook\ndescription: Agent hooks for Claude Code sessions.\n---\n"
            "# Create Hook\n\nAgent hooks for Claude Code sessions.\n",
        )

        payload = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "sess-transcript",
            "transcript_path": str(transcript),
            "prompt": "configure agent hooks",
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

        captured: dict[str, str] = {}

        def _capture_search(query: str, entries: list, *, config: dict) -> list:
            captured["query"] = query
            from cyt.skills.search import search_skills as real_search

            return real_search(query, entries, config=config)

        with patch("cyt.skills.cli.load_config", return_value=config):
            with patch("cyt.skills.cache.skills_cache_db_path", return_value=cache_path):
                with patch("cyt.config.stats_db_path", return_value=str(root / "stats.db")):
                    with patch("cyt.skills.cli.search_skills", side_effect=_capture_search):
                        from cyt.skills.cache import SessionCacheDB

                        with patch(
                            "cyt.skills.cache.skills_cache_db_path",
                            return_value=cache_path,
                        ):
                            db = SessionCacheDB.open(config)
                            try:
                                db.upsert_session("sess-transcript", "claude-sonnet-4")
                            finally:
                                db.close()
                        skills_cli.run()

        assert captured["query"] == (
            "User_Asks: configure agent hooks; Assistant_Says: prior assistant reply"
        )


@pytest.mark.parametrize("pipeline", ["bm25", "rerank", "llm"])
def test_user_prompt_uses_transcript_query_for_all_pipelines(
    monkeypatch: pytest.MonkeyPatch,
    pipeline: str,
) -> None:
    """Hook passes transcript-enriched query to search_skills for every pipeline."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skills_dir = root / "skills"
        catalog_dir = root / "catalog"
        cache_path = str(root / "cache.db")
        transcript = root / "session.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "prior assistant reply"}],
                    },
                },
            ),
            encoding="utf-8",
        )
        _write_skill(
            skills_dir / "create-hook.md",
            "---\nname: create-hook\ndescription: Agent hooks for Claude Code sessions.\n---\n"
            "# Create Hook\n\nAgent hooks for Claude Code sessions.\n",
        )

        payload = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "sess-transcript",
            "transcript_path": str(transcript),
            "prompt": "configure agent hooks",
        }
        monkeypatch.setattr("sys.stdin", StringIO(json.dumps(payload)))
        stdout = StringIO()
        monkeypatch.setattr("sys.stdout", stdout)

        config = {
            "skills": {
                "enabled": True,
                "pipeline": pipeline,
                "catalog_dir": str(catalog_dir),
                "directories": [str(skills_dir)],
                "cache": {"database": {"path": cache_path}},
                "max_tokens_per_request": 4000,
                "pageindex": {"enable_bm25_chunking": True},
            },
            "pruning": {"bm25": {"score_skills": 0.0}},
            "stats": {"database": {"path": str(root / "stats.db")}},
        }

        captured: dict[str, str] = {}

        def _capture_search(query: str, entries: list, *, config: dict) -> list:
            captured["query"] = query
            return []

        with patch("cyt.skills.cli.load_config", return_value=config):
            with patch("cyt.skills.cache.skills_cache_db_path", return_value=cache_path):
                with patch("cyt.config.stats_db_path", return_value=str(root / "stats.db")):
                    with patch("cyt.skills.cli.search_skills", side_effect=_capture_search):
                        from cyt.skills.cache import SessionCacheDB

                        db = SessionCacheDB.open(config)
                        try:
                            db.upsert_session("sess-transcript", "claude-sonnet-4")
                        finally:
                            db.close()
                        skills_cli.run()

        assert captured["query"] == (
            "User_Asks: configure agent hooks; Assistant_Says: prior assistant reply"
        )


def test_disabled_config_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache_path = str(root / "cache.db")
        payload = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "sess-abc",
            "prompt": "hello",
        }
        monkeypatch.setattr("sys.stdin", StringIO(json.dumps(payload)))
        stdout = StringIO()
        monkeypatch.setattr("sys.stdout", stdout)

        config = {
            "skills": {
                "enabled": False,
                "cache": {"database": {"path": cache_path}},
            },
        }
        with patch("cyt.skills.cli.load_config", return_value=config):
            with patch("cyt.skills.cache.skills_cache_db_path", return_value=cache_path):
                skills_cli.run()

        assert stdout.getvalue() == ""
