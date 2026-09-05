"""Tests for agent-specific .system skills filtering."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from cyt.skills.agents import (
    CYT_LAUNCH_AGENT_ENV,
    agent_from_upstream_kind,
    agent_system_skill_owner,
    is_excluded_agent_system_skill,
    resolve_skills_agent,
)
from cyt.skills.catalog import build_registry


def _write_skill(path: Path, body: str = "# Skill\n\nBody.\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_agent_system_skill_owner_detects_claude_and_codex_paths() -> None:
    claude_path = Path("/tmp/home/.claude/skills/.system/create-rule/SKILL.md")
    codex_path = Path("/project/.codex/skills/.system/autofix/SKILL.md")
    cursor_path = Path("/project/.cursor/skills/.system/demo/SKILL.md")
    user_path = Path("/tmp/home/.codex/skills/autofix/SKILL.md")

    assert agent_system_skill_owner(claude_path) == "claude"
    assert agent_system_skill_owner(codex_path) == "codex"
    assert agent_system_skill_owner(cursor_path) == "cursor"
    assert agent_system_skill_owner(user_path) is None


def test_is_excluded_agent_system_skill() -> None:
    codex_system = Path("/tmp/.codex/skills/.system/demo/SKILL.md")
    claude_system = Path("/tmp/.claude/skills/.system/demo/SKILL.md")

    assert is_excluded_agent_system_skill(codex_system, active_agent="claude")
    assert is_excluded_agent_system_skill(claude_system, active_agent="codex")
    assert not is_excluded_agent_system_skill(claude_system, active_agent="claude")
    assert not is_excluded_agent_system_skill(codex_system, active_agent=None)


def test_agent_from_upstream_kind_accepts_aliases() -> None:
    assert agent_from_upstream_kind("anthropic") == "claude"
    assert agent_from_upstream_kind("openai") == "codex"
    assert agent_from_upstream_kind("claude") == "claude"
    assert agent_from_upstream_kind("claude-code") == "claude"
    assert agent_from_upstream_kind("codex") == "codex"
    assert agent_from_upstream_kind("invalid") is None


def test_resolve_skills_agent_prefers_explicit_then_env_then_upstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(CYT_LAUNCH_AGENT_ENV, raising=False)
    assert resolve_skills_agent(agent="claude", upstream_kind="openai") == "claude"

    monkeypatch.setenv(CYT_LAUNCH_AGENT_ENV, "codex")
    assert resolve_skills_agent(upstream_kind="anthropic") == "codex"

    monkeypatch.delenv(CYT_LAUNCH_AGENT_ENV, raising=False)
    assert resolve_skills_agent(upstream_kind="anthropic") == "claude"
    assert resolve_skills_agent(upstream_kind="openai") == "codex"
    assert resolve_skills_agent(upstream_kind="claude") == "claude"
    assert resolve_skills_agent(upstream_kind="codex") == "codex"
    assert resolve_skills_agent() is None


def test_apply_proxy_skills_agent_filter_sets_launch_agent_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cyt.proxy.cli_impl import _apply_proxy_skills_agent_filter

    monkeypatch.delenv(CYT_LAUNCH_AGENT_ENV, raising=False)
    _apply_proxy_skills_agent_filter("codex")
    assert os.environ.get(CYT_LAUNCH_AGENT_ENV) == "codex"

    _apply_proxy_skills_agent_filter("claude")
    assert os.environ.get(CYT_LAUNCH_AGENT_ENV) == "claude"

    monkeypatch.delenv(CYT_LAUNCH_AGENT_ENV, raising=False)
    _apply_proxy_skills_agent_filter(None)
    assert CYT_LAUNCH_AGENT_ENV not in os.environ


def test_build_registry_excludes_other_agent_system_skills(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(CYT_LAUNCH_AGENT_ENV, raising=False)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        claude_system = root / ".claude" / "skills" / ".system" / "claude-only" / "SKILL.md"
        codex_system = root / ".codex" / "skills" / ".system" / "codex-only" / "SKILL.md"
        shared = root / ".claude" / "skills" / "shared" / "SKILL.md"
        _write_skill(claude_system, "# Claude system\n\nOnly for Claude.\n")
        _write_skill(codex_system, "# Codex system\n\nOnly for Codex.\n")
        _write_skill(shared, "# Shared\n\nFor every agent.\n")

        config = {
            "skills": {
                "enabled": True,
                "pipeline": "bm25",
                "catalog_dir": str(root / "catalog"),
                "directories": [],
                "pageindex": {"enable_bm25_chunking": True},
            },
            "agents": {
                "cursor": {"skills": {"directories": []}},
                "claude": {
                    "skills": {
                        "directories": [str(root / ".claude" / "skills")],
                    },
                },
                "codex": {
                    "skills": {
                        "directories": [str(root / ".codex" / "skills")],
                        "permissions": {
                            "deny": [
                                "path:.codex/skills/.system",
                                "path:~/.codex/skills/.system",
                            ],
                            "allow": [],
                        },
                    },
                },
            },
        }

        claude_entries = build_registry(config, agent="claude")
        codex_entries = build_registry(config, agent="codex")

        claude_paths = {entry.source_path for entry in claude_entries}
        codex_paths = {entry.source_path for entry in codex_entries}

        assert any("claude-only" in path for path in claude_paths)
        assert any("shared" in path for path in claude_paths)
        assert not any("codex-only" in path for path in claude_paths)

        assert not any("shared" in path for path in codex_paths)
        assert not any("claude-only" in path for path in codex_paths)
        assert not any("codex-only" in path for path in codex_paths)


def test_build_registry_includes_all_system_skills_without_agent_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(CYT_LAUNCH_AGENT_ENV, raising=False)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        claude_system = root / ".claude" / "skills" / ".system" / "claude-only" / "SKILL.md"
        codex_system = root / ".codex" / "skills" / ".system" / "codex-only" / "SKILL.md"
        _write_skill(claude_system, "# Claude system\n\nOnly for Claude.\n")
        _write_skill(codex_system, "# Codex system\n\nOnly for Codex.\n")

        config = {
            "skills": {
                "enabled": True,
                "pipeline": "bm25",
                "catalog_dir": str(root / "catalog"),
                "directories": [],
                "pageindex": {"enable_bm25_chunking": True},
                "permissions": {"deny": [], "allow": []},
            },
            "agents": {
                "cursor": {"skills": {"directories": [], "permissions": {"deny": [], "allow": []}}},
                "claude": {
                    "skills": {
                        "directories": [str(root / ".claude" / "skills")],
                        "permissions": {"deny": [], "allow": []},
                    },
                },
                "codex": {
                    "skills": {
                        "directories": [str(root / ".codex" / "skills")],
                        "permissions": {"deny": [], "allow": []},
                    },
                },
            },
        }

        entries = build_registry(config, agent="all")
        paths = {entry.source_path for entry in entries}
        assert any("claude-only" in path for path in paths)
        assert any("codex-only" in path for path in paths)
