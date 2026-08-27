"""Tests for cyt-client skill discovery."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from cyt_client.agent import (
    CLAUDE_CODE_ENTRYPOINT_ENV,
    CLAUDE_PROJECT_DIR_ENV,
    CLAUDECODE_ENV,
    CODEX_HOME_ENV,
    CURSOR_VERSION_ENV,
    CYT_LAUNCH_AGENT_ENV,
)
from cyt_client.skills import (
    attach_client_skills,
    collect_client_skills,
    infer_launch_agent,
    skill_directories_for_payload,
)
from cyt_client.transcript import enrich_hook_payload
from tests.conftest import isolate_user_home


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


def _write_skill(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_infer_launch_agent_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "CODEX_HOME",
        "CURSOR_VERSION",
        "CLAUDE_PROJECT_DIR",
        "CLAUDECODE",
        "CLAUDE_CODE_ENTRYPOINT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(CYT_LAUNCH_AGENT_ENV, "codex")
    assert infer_launch_agent({}) == "codex"
    monkeypatch.setenv(CYT_LAUNCH_AGENT_ENV, "claude")
    assert infer_launch_agent({}) == "claude"
    monkeypatch.setenv(CYT_LAUNCH_AGENT_ENV, "cursor")
    assert infer_launch_agent({}) == "cursor"


def test_infer_launch_agent_from_transcript_path(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "CODEX_HOME",
        "CURSOR_VERSION",
        "CLAUDE_PROJECT_DIR",
        "CLAUDECODE",
        "CLAUDE_CODE_ENTRYPOINT",
        "CYT_LAUNCH_AGENT",
    ):
        monkeypatch.delenv(name, raising=False)
    assert (
        infer_launch_agent({"transcript_path": "/Users/me/.codex/sessions/2026/rollout.jsonl"})
        == "codex"
    )
    assert (
        infer_launch_agent({"transcript_path": "/Users/me/.claude/projects/foo.jsonl"}) == "claude"
    )
    assert (
        infer_launch_agent(
            {"transcript_path": "/Users/me/.cursor/projects/foo/session.jsonl"},
        )
        == "cursor"
    )


@pytest.mark.parametrize(
    ("agent", "project_rel", "home_rel"),
    [
        ("codex", ".codex/skills", "~/.codex/skills"),
        ("claude", ".claude/skills", "~/.claude/skills"),
        ("cursor", ".cursor/skills", "~/.cursor/skills"),
    ],
)
def test_skill_directories_include_project_and_home_for_agent(
    monkeypatch: pytest.MonkeyPatch,
    agent: str,
    project_rel: str,
    home_rel: str,
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp) / "project"
        home = Path(tmp) / "home"
        _clear_harness_env(monkeypatch)
        isolate_user_home(monkeypatch, home)
        monkeypatch.setenv(CYT_LAUNCH_AGENT_ENV, agent)
        payload = {"cwd": str(project)}
        directories = skill_directories_for_payload(payload)
        assert (project / project_rel).resolve() in directories
        assert (home / home_rel.removeprefix("~/")).resolve() in {
            path.resolve() for path in directories
        }


def test_skill_directories_use_only_active_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp) / "project"
        _clear_harness_env(monkeypatch)
        isolate_user_home(monkeypatch, Path(tmp) / "home")
        monkeypatch.setenv(CYT_LAUNCH_AGENT_ENV, "cursor")
        directories = skill_directories_for_payload({"cwd": str(project)})
        joined = {path.as_posix() for path in directories}
        assert any(".cursor/skills" in path for path in joined)
        assert not any(".claude/skills" in path for path in joined)
        assert not any(".codex/skills" in path for path in joined)


def test_collect_client_skills_reads_project_and_home_for_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "home"
        project = Path(tmp) / "project"
        _clear_harness_env(monkeypatch)
        isolate_user_home(monkeypatch, home)
        monkeypatch.setenv(CYT_LAUNCH_AGENT_ENV, "cursor")

        _write_skill(
            project / ".cursor" / "skills" / "project-skill.md",
            "---\nname: project-skill\ndescription: from project\n---\n\nProject body\n",
        )
        _write_skill(
            home / ".cursor" / "skills" / "home-skill.md",
            "---\nname: home-skill\ndescription: from home\n---\n\nHome body\n",
        )

        skills = collect_client_skills({"cwd": str(project)})
        paths = {skill["path"] for skill in skills}
        assert str((project / ".cursor" / "skills" / "project-skill.md").resolve()) in paths
        assert str((home / ".cursor" / "skills" / "home-skill.md").resolve()) in paths


def test_collect_client_skills_reads_project_and_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "home"
        project = Path(tmp) / "project"
        _clear_harness_env(monkeypatch)
        isolate_user_home(monkeypatch, home)
        monkeypatch.setenv(CYT_LAUNCH_AGENT_ENV, "codex")

        _write_skill(
            project / ".codex" / "skills" / "project-skill.md",
            "---\nname: project-skill\ndescription: from project\n---\n\nProject body\n",
        )
        _write_skill(
            home / ".codex" / "skills" / "home-skill.md",
            "---\nname: home-skill\ndescription: from home\n---\n\nHome body\n",
        )

        skills = collect_client_skills({"cwd": str(project)})
        paths = {skill["path"] for skill in skills}
        assert str((project / ".codex" / "skills" / "project-skill.md").resolve()) in paths
        assert str((home / ".codex" / "skills" / "home-skill.md").resolve()) in paths
        assert all("content" in skill for skill in skills)


def test_collect_client_skills_dedupes_identical_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "home"
        project = Path(tmp) / "project"
        _clear_harness_env(monkeypatch)
        isolate_user_home(monkeypatch, home)
        monkeypatch.setenv(CYT_LAUNCH_AGENT_ENV, "codex")
        body = "---\nname: dup\ndescription: dup\n---\n\nSame\n"
        _write_skill(project / ".codex" / "skills" / "a.md", body)
        _write_skill(project / ".codex" / "skills" / "nested" / "b.md", body)
        skills = collect_client_skills({"cwd": str(project)})
        assert len(skills) == 1


def test_attach_client_skills_sets_payload_field(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "home"
        project = Path(tmp) / "project"
        _clear_harness_env(monkeypatch)
        isolate_user_home(monkeypatch, home)
        monkeypatch.setenv(CYT_LAUNCH_AGENT_ENV, "codex")
        _write_skill(
            project / ".codex" / "skills" / "demo.md",
            "---\nname: demo\ndescription: demo\n---\n\nDemo\n",
        )
        payload = attach_client_skills({"cwd": str(project), "prompt": "hello"})
        assert "cyt_skills" in payload
        assert len(payload["cyt_skills"]) == 1


def test_enrich_hook_payload_always_adds_cyt_skills(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _clear_harness_env(monkeypatch)
        isolate_user_home(monkeypatch, Path(tmp) / "home")
        monkeypatch.setenv(CYT_LAUNCH_AGENT_ENV, "codex")
        payload = {
            "hook_event_name": "UserPromptSubmit",
            "prompt": "hello",
            "cwd": str(Path(tmp) / "project"),
        }
        enriched = json.loads(enrich_hook_payload(json.dumps(payload).encode()))
        assert "cyt_skills" in enriched
        assert isinstance(enriched["cyt_skills"], list)


def test_enrich_hook_payload_adds_transcript_and_skills(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "home"
        transcript = Path(tmp) / "session.jsonl"
        transcript.write_text('{"id": 1}\n', encoding="utf-8")
        project = Path(tmp) / "project"
        _clear_harness_env(monkeypatch)
        isolate_user_home(monkeypatch, home)
        monkeypatch.setenv(CYT_LAUNCH_AGENT_ENV, "codex")
        _write_skill(
            project / ".codex" / "skills" / "demo.md",
            "---\nname: demo\ndescription: demo\n---\n\nDemo\n",
        )
        payload = {
            "hook_event_name": "UserPromptSubmit",
            "cwd": str(project),
            "transcript_path": str(transcript),
            "prompt": "hello",
        }
        enriched = json.loads(enrich_hook_payload(json.dumps(payload).encode()))
        assert enriched["cyt_transcript"] == [{"id": 1}]
        assert len(enriched["cyt_skills"]) == 1
