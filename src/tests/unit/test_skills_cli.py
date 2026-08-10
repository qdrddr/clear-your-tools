"""Tests for the hook handler (``cyt.skills.cli --stdin``)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from collections.abc import Generator
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

from cyt.launch.secrets import clear_keyring_cache
from cyt.skills import cli as skills_cli
from tests.support.credential_helpers import (
    apply_ci_credential_stubs,
    clear_credential_env_var,
    install_test_pre_dotenv,
    isolate_credential_env_paths,
)


@pytest.fixture(autouse=True)
def _reset_credential_caches() -> Generator[None]:
    clear_keyring_cache()
    yield
    clear_keyring_cache()


@pytest.fixture(autouse=True)
def _track_shell_exports(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    isolate_credential_env_paths(monkeypatch, tmp_path, chdir=False)
    install_test_pre_dotenv(monkeypatch)
    apply_ci_credential_stubs(monkeypatch)


def _write_skill(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _skills_config(root: Path, skills_dir: Path, catalog_dir: Path) -> dict:
    return {
        "skills": {
            "enabled": True,
            "pipeline": "bm25",
            "catalog_dir": str(catalog_dir),
            "directories": [str(skills_dir)],
            "max_tokens_per_request": 4000,
            "pageindex": {"enable_bm25_chunking": True},
            "hook": {
                "request_budget_fraction": 50.0,
                "inject_cap_multiplier_of_request_tokens": 5.0,
            },
        },
        "pruning": {
            "inject_via": {"cursor": "hook", "claude": "hook", "codex": "hook"},
            "tools": {
                "sequence": ["bm25"],
                "pipelines": {"bm25": {"score_skills": 0.0}},
                "hook": {
                    "tools_from": "definitions",
                    "mcp_definitions_file": str(root / "missing-tools.json"),
                },
            },
        },
        "stats": {"database": {"path": str(root / "stats.db")}},
    }


def _write_transcript_with_model(path: Path, model: str) -> None:
    path.write_text(
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "model": model,
                    "content": [{"type": "text", "text": "prior reply"}],
                },
            },
        ),
        encoding="utf-8",
    )


def test_anthropic_user_prompt_resolves_model_from_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Claude Code: UserPromptSubmit has no model; transcript_path supplies it."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skills_dir = root / "skills"
        catalog_dir = root / "catalog"
        transcript = root / "session.jsonl"
        _write_transcript_with_model(transcript, "google/gemini-3-flash-preview-20251217")
        _write_skill(
            skills_dir / "create-hook.md",
            "---\nname: create-hook\ndescription: Agent hooks for Claude Code sessions.\n---\n"
            "# Create Hook\n\nAgent hooks for Claude Code sessions.\n",
        )

        prompt_payload = {
            "session_id": "11b09b4b-f335-4a08-b618-8f607f6d7a46",
            "transcript_path": str(transcript),
            "cwd": str(root),
            "permission_mode": "default",
            "hook_event_name": "UserPromptSubmit",
            "prompt": "configure agent hooks for sessions",
        }
        monkeypatch.setattr("sys.stdin", StringIO(json.dumps(prompt_payload)))
        stdout = StringIO()
        monkeypatch.setattr("sys.stdout", stdout)

        config = _skills_config(root, skills_dir, catalog_dir)
        with patch("cyt.skills.cli.load_config", return_value=config):
            with patch("cyt.config.stats_db_path", return_value=str(root / "stats.db")):
                skills_cli.run(debug=True)

        output = json.loads(stdout.getvalue())
        assert output["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
        assert "<agent-skills>" in output["hookSpecificOutput"]["additionalContext"]

        from cyt.proxy.stats import StatsDB

        db = StatsDB.open(str(root / "stats.db"))
        try:
            row = db._conn.execute(
                "SELECT skills_final_md FROM proxy_request WHERE endpoint = 'skills-hook'",
            ).fetchone()
            assert row is not None
            assert row[0] is not None
            assert "<agent-skills>" in row[0]
        finally:
            db.close()

        assert not (root / ".debug" / "skills").exists()


def test_nested_payload_user_prompt_submit(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skills_dir = root / "skills"
        catalog_dir = root / "catalog"
        transcript = root / "session.jsonl"
        _write_transcript_with_model(transcript, "claude-sonnet-4")
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
                "transcript_path": str(transcript),
            },
        }
        monkeypatch.setattr("sys.stdin", StringIO(json.dumps(payload)))
        stdout = StringIO()
        monkeypatch.setattr("sys.stdout", stdout)

        config = _skills_config(root, skills_dir, catalog_dir)
        with patch("cyt.skills.cli.load_config", return_value=config):
            skills_cli.run()

        output = json.loads(stdout.getvalue())
        assert output["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
        assert "<agent-skills>" in output["hookSpecificOutput"]["additionalContext"]


def test_session_start_is_ignored_without_output(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload = {
            "hook_event_name": "SessionStart",
            "session_id": "sess-abc",
            "model": "claude-sonnet-4",
            "cwd": str(root),
        }
        monkeypatch.setattr("sys.stdin", StringIO(json.dumps(payload)))
        stdout = StringIO()
        monkeypatch.setattr("sys.stdout", stdout)
        monkeypatch.chdir(root)

        config = {"skills": {"enabled": True}}
        from cyt.hook.daemon import HookDaemonStartResult

        with (
            patch("cyt.skills.cli.load_config", return_value=config),
            patch(
                "cyt.hook.daemon.daemon_start",
                return_value=HookDaemonStartResult(
                    outcome="reused",
                    port=8834,
                    hook_url="http://127.0.0.1:8834/hook/inject",
                    pid=None,
                    reused=True,
                ),
            ),
        ):
            skills_cli.run(debug=True)

        assert stdout.getvalue() == ""
        assert not (root / ".debug" / "skills").exists()


def test_user_prompt_emits_json_hook_output(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skills_dir = root / "skills"
        catalog_dir = root / "catalog"
        transcript = root / "session.jsonl"
        _write_transcript_with_model(transcript, "claude-sonnet-4")
        _write_skill(
            skills_dir / "create-hook.md",
            "---\nname: create-hook\ndescription: Agent hooks for Claude Code sessions.\n---\n"
            "# Create Hook\n\nAgent hooks for Claude Code sessions.\n",
        )

        payload = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "sess-abc",
            "transcript_path": str(transcript),
            "prompt": "configure agent hooks for sessions",
        }
        monkeypatch.setattr("sys.stdin", StringIO(json.dumps(payload)))
        stdout = StringIO()
        monkeypatch.setattr("sys.stdout", stdout)

        config = _skills_config(root, skills_dir, catalog_dir)
        with patch("cyt.skills.cli.load_config", return_value=config):
            with patch("cyt.config.stats_db_path", return_value=str(root / "stats.db")):
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
            "cwd": str(root),
        }
        monkeypatch.setattr("sys.stdin", StringIO(json.dumps(payload)))
        stdout = StringIO()
        monkeypatch.setattr("sys.stdout", stdout)
        monkeypatch.chdir(root)

        config = _skills_config(root, skills_dir, catalog_dir)
        with patch("cyt.skills.cli.load_config", return_value=config):
            with patch("cyt.config.stats_db_path", return_value=str(root / "stats.db")):
                skills_cli.run(debug=True)

        from cyt.proxy.stats import StatsDB

        db = StatsDB.open(str(root / "stats.db"))
        try:
            row = db._conn.execute(
                "SELECT skills_final_md, mr.model_name "
                "FROM proxy_request pr "
                "JOIN model_request mr ON mr.proxy_request_id = pr.id "
                "WHERE pr.endpoint = 'skills-hook'",
            ).fetchone()
            assert row is not None
            assert row[0] is not None
            assert row[1] == "gpt-5.4-mini"
        finally:
            db.close()

        assert not (root / ".debug" / "skills").exists()

        output = json.loads(stdout.getvalue())
        assert output["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
        assert "<agent-skills>" in output["hookSpecificOutput"]["additionalContext"]


def test_cli_prompt_prints_injection_text(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skills_dir = root / "skills"
        catalog_dir = root / "catalog"
        _write_skill(
            skills_dir / "context7.md",
            "---\nname: context7\ndescription: Use Context7 MCP for up-to-date library documentation.\n---\n"
            "# Context7\n\nUse Context7 MCP for up-to-date library documentation.\n",
        )

        stdout = StringIO()
        monkeypatch.setattr("sys.stdout", stdout)
        monkeypatch.chdir(root)

        config = _skills_config(root, skills_dir, catalog_dir)
        config["skills"]["frontmatter_upper_limit"] = 0.99
        with patch("cyt.skills.cli.load_config", return_value=config):
            skills_cli.run(prompt="use context7 library docs")

        output = stdout.getvalue()
        assert "<agent-skills>" in output
        assert "context7" in output
        assert "hookSpecificOutput" not in output


def test_cli_prompt_reports_configured_and_executed_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skills_dir = root / "skills"
        catalog_dir = root / "catalog"
        _write_skill(
            skills_dir / "context7.md",
            "---\nname: context7\ndescription: Use Context7 MCP for up-to-date library documentation.\n---\n"
            "# Context7\n\nUse Context7 MCP for up-to-date library documentation.\n",
        )

        stdout = StringIO()
        monkeypatch.setattr("sys.stdout", stdout)
        monkeypatch.chdir(root)

        config = _skills_config(root, skills_dir, catalog_dir)
        config["skills"]["pipeline"] = "rerank"
        config["skills"]["frontmatter_upper_limit"] = 0.99
        with patch("cyt.skills.cli.load_config", return_value=config):
            skills_cli.run(prompt="use context7 library docs")

        err = capsys.readouterr().err
        assert "skills.pipeline (configured): rerank" in err
        assert "skills.pipeline (executed): bm25" in err
        assert "skills.pipeline fallback:" in err
        assert "bm25_node_fallback_threshold" in err


def test_cli_prompt_reports_frontmatter_and_chunk_scores(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skills_dir = root / "skills"
        catalog_dir = root / "catalog"
        _write_skill(
            skills_dir / "context7.md",
            "---\nname: context7\ndescription: Use Context7 MCP for up-to-date library documentation.\n---\n"
            "# Context7\n\nUse Context7 MCP for up-to-date library documentation.\n",
        )

        stdout = StringIO()
        monkeypatch.setattr("sys.stdout", stdout)
        monkeypatch.chdir(root)

        config = _skills_config(root, skills_dir, catalog_dir)
        config["skills"]["frontmatter_upper_limit"] = 0.99
        with patch("cyt.skills.cli.load_config", return_value=config):
            skills_cli.run(prompt="use context7 library docs")

        err = capsys.readouterr().err
        assert "skills.frontmatter gate (BM25 similarity [0-1]" in err
        assert "score=" in err
        assert "\nskills.search (chunk" in err
        assert "chunk  score" in err or "chunk    score" in err


def test_cli_prompt_debug_prints_blocked_gate_and_final_injection(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skills_dir = root / "skills"
        catalog_dir = root / "catalog"
        _write_skill(
            skills_dir / "context7.md",
            "---\nname: context7\ndescription: Use Context7 MCP for up-to-date library documentation.\n---\n"
            "# Context7\n\nUse Context7 MCP for up-to-date library documentation.\n",
        )
        _write_skill(
            skills_dir / "other.md",
            "---\nname: other\ndescription: Unrelated database topic.\n---\n"
            "# Other\n\nDatabase shard rebalancing only.\n",
        )

        stdout = StringIO()
        monkeypatch.setattr("sys.stdout", stdout)
        monkeypatch.chdir(root)

        config = _skills_config(root, skills_dir, catalog_dir)
        with patch("cyt.skills.cli.load_config", return_value=config):
            skills_cli.run(prompt="use context7 library docs", debug=True)

        captured = capsys.readouterr()
        assert "<agent-skills>" not in captured.err
        output = stdout.getvalue()
        assert output.count("<agent-skills>") == 1
        assert "<agent-skills>" in output


def test_cli_prompt_debug_prints_frontmatter_token_contributions(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skills_dir = root / "skills"
        catalog_dir = root / "catalog"
        _write_skill(
            skills_dir / "context7.md",
            "---\nname: context7\ndescription: Use Context7 MCP for up-to-date library documentation.\n---\n"
            "# Context7\n\nUse Context7 MCP for up-to-date library documentation.\n",
        )
        _write_skill(
            skills_dir / "other.md",
            "---\nname: other\ndescription: Unrelated database topic.\n---\n"
            "# Other\n\nDatabase shard rebalancing only.\n",
        )

        stdout = StringIO()
        monkeypatch.setattr("sys.stdout", stdout)
        monkeypatch.chdir(root)

        config = _skills_config(root, skills_dir, catalog_dir)
        with patch("cyt.skills.cli.load_config", return_value=config):
            skills_cli.run(prompt="use context7 library docs", debug=True)

        err = capsys.readouterr().err
        assert "score=" in err
        assert "blocked" in err or "pass" in err
        assert "skills.search (chunk" in err


def test_debug_logs_stdin_when_skills_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload = {
            "hook_event_name": "SessionStart",
            "session_id": "sess-debug",
            "model": "gpt-4",
            "cwd": str(root),
        }
        monkeypatch.setattr("sys.stdin", StringIO(json.dumps(payload)))
        stdout = StringIO()
        monkeypatch.setattr("sys.stdout", stdout)

        config = {"skills": {"enabled": False}}
        from cyt.hook.daemon import HookDaemonStartResult

        with (
            patch("cyt.skills.cli.load_config", return_value=config),
            patch(
                "cyt.hook.daemon.daemon_start",
                return_value=HookDaemonStartResult(
                    outcome="reused",
                    port=8834,
                    hook_url="http://127.0.0.1:8834/hook/inject",
                    pid=None,
                    reused=True,
                ),
            ),
        ):
            skills_cli.run(debug=True)

        assert not (root / ".debug" / "skills").exists()


def test_hook_stdin_debug_writes_db_without_terminal_logs(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skills_dir = root / "skills"
        catalog_dir = root / "catalog"
        _write_skill(
            skills_dir / "create-hook.md",
            "---\nname: create-hook\ndescription: Agent hooks for Claude Code sessions.\n---\n"
            "# Create Hook\n\nAgent hooks for Claude Code sessions.\n",
        )

        payload = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "sess-debug-db",
            "model": "gpt-5",
            "prompt": "configure agent hooks",
            "cwd": str(root),
        }
        monkeypatch.setattr("sys.stdin", StringIO(json.dumps(payload)))
        stdout = StringIO()
        monkeypatch.setattr("sys.stdout", stdout)
        monkeypatch.chdir(root)

        config = _skills_config(root, skills_dir, catalog_dir)
        with patch("cyt.skills.cli.load_config", return_value=config):
            with patch("cyt.config.stats_db_path", return_value=str(root / "stats.db")):
                skills_cli.run(debug=True)

        captured = capsys.readouterr()
        assert captured.err == ""
        assert (
            json.loads(stdout.getvalue())["hookSpecificOutput"]["hookEventName"]
            == "UserPromptSubmit"
        )
        assert not (root / ".debug" / "skills").exists()

        from cyt.proxy.stats import StatsDB

        db = StatsDB.open(str(root / "stats.db"))
        try:
            row = db._conn.execute(
                "SELECT skills_final_md FROM proxy_request WHERE endpoint = 'skills-hook'",
            ).fetchone()
            assert row is not None
            assert row[0] is not None
        finally:
            db.close()


def test_cli_prompt_runs_when_skills_disabled_in_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skills_dir = root / "skills"
        catalog_dir = root / "catalog"
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
                "max_tokens_per_request": 4000,
                "frontmatter_upper_limit": 0.99,
                "pageindex": {"enable_bm25_chunking": True},
                "hook": {
                    "request_budget_fraction": 50.0,
                    "inject_cap_multiplier_of_request_tokens": 5.0,
                },
            },
            "pruning": {
                "inject_via": {"cursor": "hook", "claude": "hook", "codex": "hook"},
                "tools": {"pipelines": {"bm25": {"score_skills": 0.0}}},
            },
            "stats": {"database": {"path": str(root / "stats.db")}},
        }

        with patch("cyt.skills.cli.load_config", return_value=config):
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
        transcript = root / "session.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "model": "claude-sonnet-4",
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

        config = _skills_config(root, skills_dir, catalog_dir)
        captured: dict[str, str] = {}

        def _capture_search(
            query: str,
            entries: list,
            *,
            config: dict,
            max_tokens: int | None = None,
            pruner_settings: object | None = None,
            skip_frontmatter_gate: bool = False,
        ) -> list:
            del pruner_settings, skip_frontmatter_gate
            captured["query"] = query
            from cyt.skills.search import search_skills as real_search

            return real_search(query, entries, config=config, max_tokens=max_tokens)

        with patch("cyt.skills.cli.load_config", return_value=config):
            with patch("cyt.config.stats_db_path", return_value=str(root / "stats.db")):
                with patch("cyt.skills.cli.search_skills", side_effect=_capture_search):
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
        transcript = root / "session.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "message": {
                        "role": "assistant",
                        "model": "claude-sonnet-4",
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

        config = _skills_config(root, skills_dir, catalog_dir)
        config["skills"]["pipeline"] = pipeline
        if pipeline == "rerank":
            monkeypatch.setenv("DEEPINFRA_API_KEY", "test-key")
        elif pipeline == "llm":
            monkeypatch.setenv("OPENROUTER_" + "API_KEY", "test-key")
        monkeypatch.setattr("cyt.launch.secrets._read_keyring", lambda _name: None)
        captured: dict[str, str] = {}

        def _capture_search(
            query: str,
            entries: list,
            *,
            config: dict,
            max_tokens: int | None = None,
            pruner_settings: object | None = None,
            skip_frontmatter_gate: bool = False,
        ) -> list:
            del config, pruner_settings, skip_frontmatter_gate
            captured["query"] = query
            _ = max_tokens
            return []

        with patch("cyt.skills.cli.load_config", return_value=config):
            with patch("cyt.config.stats_db_path", return_value=str(root / "stats.db")):
                with patch("cyt.skills.cli.search_skills", side_effect=_capture_search):
                    skills_cli.run()

        assert captured["query"] == (
            "User_Asks: configure agent hooks; Assistant_Says: prior assistant reply"
        )


def test_disabled_config_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "hook_event_name": "UserPromptSubmit",
        "session_id": "sess-abc",
        "prompt": "hello",
    }
    monkeypatch.setattr("sys.stdin", StringIO(json.dumps(payload)))
    stdout = StringIO()
    monkeypatch.setattr("sys.stdout", stdout)

    config = {
        "skills": {"enabled": False},
        "pruning": {"tools": {"enabled": False}},
    }
    with patch("cyt.skills.cli.load_config", return_value=config):
        skills_cli.run()

    assert stdout.getvalue() == ""


def test_hook_stdout_is_pure_json_when_search_prints_to_stdout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pruning diagnostics must not prefix hook stdout or Codex falls back to plain text."""
    from cyt.indexer.tokens import log_token_usage

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skills_dir = root / "skills"
        catalog_dir = root / "catalog"
        _write_skill(
            skills_dir / "create-hook.md",
            "---\nname: create-hook\ndescription: Agent hooks for Claude Code sessions.\n---\n"
            "# Create Hook\n\nAgent hooks for Claude Code sessions.\n",
        )

        payload = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "sess-pure-json",
            "model": "gpt-5",
            "prompt": "configure agent hooks",
            "cwd": str(root),
        }
        monkeypatch.setattr("sys.stdin", StringIO(json.dumps(payload)))
        stdout = StringIO()
        monkeypatch.setattr("sys.stdout", stdout)
        monkeypatch.chdir(root)

        real_search = skills_cli.search_skills

        def _noisy_search(
            query: str,
            entries: list,
            *,
            config: dict,
            max_tokens: int | None = None,
            pruner_settings: object | None = None,
            skip_frontmatter_gate: bool = False,
        ) -> list:
            del pruner_settings, skip_frontmatter_gate
            print("pruning model tokens (llm): 999 tokens", flush=True)
            log_token_usage("pruning model tokens (llm)", 999)
            return real_search(query, entries, config=config, max_tokens=max_tokens)

        config = _skills_config(root, skills_dir, catalog_dir)
        with patch("cyt.skills.cli.load_config", return_value=config):
            with patch("cyt.config.stats_db_path", return_value=str(root / "stats.db")):
                with patch("cyt.skills.cli.search_skills", side_effect=_noisy_search):
                    skills_cli.run()

        raw = stdout.getvalue()
        output = json.loads(raw)
        assert raw.strip().startswith("{")
        assert raw.strip().endswith("}")
        assert output["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
        assert "<agent-skills>" in output["hookSpecificOutput"]["additionalContext"]
        assert "pruning model tokens" not in raw
        assert "hookSpecificOutput" not in output["hookSpecificOutput"]["additionalContext"]


def test_skills_test_reports_required_keys(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = {
        "skills": {"enabled": True, "pipeline": "llm"},
        "pruning": {
            "tools": {
                "sequence": ["bm25"],
                "pipelines": {"llm": {"model_nick": "mercury-2"}},
            },
        },
        "models": {
            "llm": {
                "remote": [
                    {
                        "nick": "mercury-2",
                        "name": "inception/mercury-2",
                        "provider": "openrouter",
                        "key_var_name": "OPENROUTER_" + "API_KEY",
                    },
                ],
            },
        },
    }
    monkeypatch.setattr("cyt.config.load_user_config_overlay", lambda _path=None: {})
    monkeypatch.setattr("cyt.launch.secrets._read_keyring", lambda _name: None)
    monkeypatch.setenv("OPENROUTER_" + "API_KEY", "from-shell")

    with patch("cyt.skills.cli.load_config", return_value=config):
        skills_cli.run(test=True)

    out = capsys.readouterr().out
    assert "skills.enabled: True" in out
    assert "skills.pipeline (configured): llm" in out
    assert "pruning.pipeline (configured): ['bm25']" in out
    assert "Skills API keys:" in out
    assert "Pruning API keys:" in out
    assert "All required API keys:" in out
    assert "OPENROUTER_" + "API_KEY" in out
    assert "env: shell" in out


def test_skills_cli_main_accepts_hook_test_flag(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = {
        "skills": {"enabled": True, "pipeline": "llm"},
        "pruning": {
            "tools": {
                "sequence": ["bm25"],
                "pipelines": {"llm": {"model_nick": "mercury-2"}},
            },
        },
        "models": {
            "llm": {
                "remote": [
                    {
                        "nick": "mercury-2",
                        "name": "inception/mercury-2",
                        "provider": "openrouter",
                        "key_var_name": "OPENROUTER_" + "API_KEY",
                    },
                ],
            },
        },
    }
    monkeypatch.setattr(sys, "argv", ["cli.py", "hook", "--test"])
    monkeypatch.setattr("cyt.config.load_user_config_overlay", lambda _path=None: {})
    monkeypatch.setattr("cyt.launch.secrets._read_keyring", lambda _name: None)
    monkeypatch.setenv("OPENROUTER_" + "API_KEY", "from-shell")

    with patch("cyt.skills.cli.load_config", return_value=config):
        skills_cli.main()

    out = capsys.readouterr().out
    assert "All required API keys:" in out
    assert "OPENROUTER_" + "API_KEY" in out


def test_skills_test_reports_pruning_pipeline_keys(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = {
        "skills": {"enabled": False, "pipeline": "bm25"},
        "pruning": {
            "tools": {
                "sequence": ["llm"],
                "pipelines": {"llm": {"model_nick": "mercury-2"}},
            },
        },
        "models": {
            "llm": {
                "remote": [
                    {
                        "nick": "mercury-2",
                        "name": "inception/mercury-2",
                        "provider": "openrouter",
                        "key_var_name": "OPENROUTER_" + "API_KEY",
                    },
                ],
            },
        },
    }
    monkeypatch.setattr("cyt.config.load_user_config_overlay", lambda _path=None: {})
    monkeypatch.setattr("cyt.launch.secrets._read_keyring", lambda _name: None)
    monkeypatch.setenv("OPENROUTER_" + "API_KEY", "from-shell")

    with patch("cyt.skills.cli.load_config", return_value=config):
        skills_cli.run(test=True)

    out = capsys.readouterr().out
    assert "skills.enabled: False" in out
    assert "pruning.pipeline (configured): ['llm']" in out
    assert "Skills API keys: (none — skills disabled)" in out
    assert "Pruning API keys:" in out
    assert "OPENROUTER_" + "API_KEY" in out
    assert "env: shell" in out


def test_hook_resolves_skills_key_from_keyring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skills_dir = root / "skills"
        catalog_dir = root / "catalog"
        _write_skill(
            skills_dir / "demo.md",
            "---\nname: demo\ndescription: demo skill\n---\n# Demo\n\nBody text.\n",
        )
        config = {
            "skills": {
                "enabled": True,
                "pipeline": "llm",
                "catalog_dir": str(catalog_dir),
                "directories": [str(skills_dir)],
                "max_tokens_per_request": 4000,
            },
            "pruning": {
                "tools": {
                    "sequence": ["bm25"],
                    "pipelines": {"llm": {"model_nick": "mercury-2"}},
                },
            },
            "models": {
                "llm": {
                    "remote": [
                        {
                            "nick": "mercury-2",
                            "name": "inception/mercury-2",
                            "provider": "openrouter",
                            "key_var_name": "OPENROUTER_" + "API_KEY",
                        },
                    ],
                },
            },
            "stats": {"database": {"path": str(root / "stats.db")}},
        }
        monkeypatch.setattr("cyt.config.load_user_config_overlay", lambda _path=None: {})
        clear_credential_env_var(monkeypatch, "OPENROUTER_" + "API_KEY")
        monkeypatch.setattr(
            "cyt.launch.secrets._read_keyring",
            lambda name: "keyring-secret" if name == "OPENROUTER_" + "API_KEY" else None,
        )
        monkeypatch.setattr(
            "cyt.skills.llm.llm_skill_nodes",
            lambda query, entries, config=None: ([], {}),
        )
        payload = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "sess-keyring",
            "model": "gpt-4",
            "prompt": "demo skill",
            "cwd": str(root),
        }
        monkeypatch.setattr("sys.stdin", StringIO(json.dumps(payload)))

        with patch("cyt.skills.cli.load_config", return_value=config):
            skills_cli.run()

        assert os.environ.get("OPENROUTER_" + "API_KEY") == "keyring-secret"


def test_hook_stdin_dispatches_to_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    """Development ``cyt.skills.cli --stdin`` runs the hook handler."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skills_dir = root / "skills"
        catalog_dir = root / "catalog"
        _write_skill(
            skills_dir / "create-hook.md",
            "---\nname: create-hook\ndescription: Agent hooks for Claude Code sessions.\n---\n"
            "# Create Hook\n\nAgent hooks for Claude Code sessions.\n",
        )

        payload = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "hook-stdin-sess",
            "model": "gpt-5.4-mini",
            "prompt": "configure agent hooks for sessions",
            "cwd": str(root),
        }
        monkeypatch.setattr("sys.stdin", StringIO(json.dumps(payload)))
        stdout = StringIO()
        monkeypatch.setattr("sys.stdout", stdout)
        monkeypatch.setattr("sys.argv", ["cyt.skills.cli", "--stdin"])

        config = _skills_config(root, skills_dir, catalog_dir)
        with patch("cyt.skills.cli.load_config", return_value=config):
            with patch("cyt.config.stats_db_path", return_value=str(root / "stats.db")):
                skills_cli.main()

        output = json.loads(stdout.getvalue())
        assert output["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
        assert "<agent-skills>" in output["hookSpecificOutput"]["additionalContext"]


def test_hook_records_stats_when_model_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skills_dir = root / "skills"
        catalog_dir = root / "catalog"
        _write_skill(
            skills_dir / "create-hook.md",
            "---\nname: create-hook\ndescription: Agent hooks for Claude Code sessions.\n---\n"
            "# Create Hook\n\nAgent hooks for Claude Code sessions.\n",
        )
        config = _skills_config(root, skills_dir, catalog_dir)
        payload = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "hook-no-model",
            "prompt": "configure agent hooks for sessions",
            "cwd": str(root),
        }
        monkeypatch.setattr("sys.stdin", StringIO(json.dumps(payload)))
        stdout = StringIO()
        monkeypatch.setattr("sys.stdout", stdout)

        with patch("cyt.skills.cli.load_config", return_value=config):
            with patch("cyt.config.stats_db_path", return_value=str(root / "stats.db")):
                skills_cli.run()

        from cyt.proxy.stats import StatsDB

        db = StatsDB.open(str(root / "stats.db"))
        try:
            row = db._conn.execute(
                """
                SELECT model_request.model_name, proxy_request.skills_in, proxy_request.request_tokens
                FROM model_request
                JOIN proxy_request ON model_request.proxy_request_id = proxy_request.id
                WHERE proxy_request.endpoint IN ('skills', 'skills-hook')
                """,
            ).fetchone()
            assert row is not None
            assert row[0] == "hook"
            assert int(row[1]) > 0
            assert int(row[2]) > 0
        finally:
            db.close()


def test_format_hook_stdout_includes_session_log_and_agent() -> None:
    from cyt.skills.cli import format_hook_stdout

    payload = {"hook_event_name": "UserPromptSubmit"}
    stdout = format_hook_stdout(
        "injection",
        payload,
        session_log=[{"kind": "tool", "key": "tool:Shell", "name": "Shell"}],
        cyt_agent="cursor",
    )
    data = json.loads(stdout)
    assert data["cytAgent"] == "cursor"
    assert data["cytSessionLog"][0]["name"] == "Shell"
    assert data["hookSpecificOutput"]["additionalContext"] == "injection"
