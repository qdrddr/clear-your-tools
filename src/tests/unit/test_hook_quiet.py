"""Tests for hook stdout quiet helpers."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

from cyt.skills import hook_quiet


def test_configure_hook_quiet_is_idempotent() -> None:
    hook_quiet.configure_hook_quiet()
    hook_quiet.configure_hook_quiet()


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
            },
        },
        "stats": {"database": {"path": str(root / "stats.db")}},
    }


def test_hook_stdout_is_pure_json_when_bm25_tokenize_uses_tqdm(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """BM25 tokenization progress must not prefix hook stdout."""
    from cyt.skills import cli as skills_cli

    root = tmp_path
    skills_dir = root / "skills"
    catalog_dir = root / "catalog"
    _write_skill(
        skills_dir / "create-hook.md",
        "---\nname: create-hook\ndescription: Agent hooks for Claude Code sessions.\n---\n"
        "# Create Hook\n\nAgent hooks for Claude Code sessions.\n",
    )

    payload = {
        "hook_event_name": "UserPromptSubmit",
        "session_id": "sess-bm25-tqdm",
        "model": "gpt-5",
        "prompt": "configure agent hooks",
        "cwd": str(root),
    }
    monkeypatch.setattr("sys.stdin", StringIO(json.dumps(payload)))
    stdout = StringIO()
    monkeypatch.setattr("sys.stdout", stdout)
    monkeypatch.chdir(root)

    config = _skills_config(root, skills_dir, catalog_dir)
    config["skills"]["pipeline"] = "bm25"
    with patch("cyt.skills.cli.load_config", return_value=config):
        with patch("cyt.config.stats_db_path", return_value=str(root / "stats.db")):
            skills_cli.run()

    raw = stdout.getvalue()
    output = json.loads(raw)
    assert raw.strip().startswith("{")
    assert "Tokenize texts" not in raw
    assert "<agent-skills>" in output["hookSpecificOutput"]["additionalContext"]
