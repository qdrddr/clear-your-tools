"""Tests for hook skills supplied by cyt-client."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from cyt.hook.workspace_config import set_hook_workspace_in_config
from cyt.skills.catalog import build_registry, clear_registry_cache
from cyt.skills.client_skills import build_registry_for_hook_payload, client_skills_from_payload
from tests.conftest import isolate_user_home
from tests.support.skills_helpers import isolated_skills_agents_block


def _write_skill(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _skills_config(root: Path, skills_dir: Path, catalog_dir: Path) -> dict:
    config = {
        "cache": {"skills_dir": str(catalog_dir)},
        "skills": {
            "enabled": True,
            "pipeline": "bm25",
            "catalog_dir": str(catalog_dir),
            "directories": [str(skills_dir)],
        },
        "agents": isolated_skills_agents_block(),
    }
    return set_hook_workspace_in_config(config, root)


def test_client_skills_from_payload_requires_key() -> None:
    assert client_skills_from_payload({}) is None
    assert client_skills_from_payload({"cyt_skills": []}) == []


def test_build_registry_for_hook_payload_uses_client_skills_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        isolate_user_home(monkeypatch, root / "home")
        config_dir = root / "config-skills"
        client_dir = root / "client-skills"
        catalog_dir = root / "catalog"
        config_dir.mkdir()
        client_dir.mkdir()
        catalog_dir.mkdir()

        _write_skill(
            config_dir / "config-only.md",
            "---\nname: config-only\ndescription: config\n---\n\nConfig body\n",
        )
        client_path = client_dir / "client-only.md"
        client_body = "---\nname: client-only\ndescription: client\n---\n\nClient body\n"
        _write_skill(client_path, client_body)

        config = _skills_config(root, config_dir, catalog_dir)
        payload = {
            "cyt_skills": [
                {"path": str(client_path.resolve()), "content": client_body},
            ],
        }

        clear_registry_cache()
        entries = build_registry_for_hook_payload(config, payload)
        doc_ids = {entry.doc_id for entry in entries}
        assert doc_ids == {"client-only"}
        assert entries[0].source_path == str(client_path.resolve())

        clear_registry_cache()
        config_entries = build_registry(config)
        assert {entry.doc_id for entry in config_entries} == {"config-only"}


def test_build_registry_for_hook_payload_empty_when_client_skills_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        isolate_user_home(monkeypatch, root / "home")
        skills_dir = root / "skills"
        catalog_dir = root / "catalog"
        skills_dir.mkdir()
        catalog_dir.mkdir()
        _write_skill(
            skills_dir / "from-dirs.md",
            "---\nname: from-dirs\ndescription: dirs\n---\n\nBody\n",
        )
        config = _skills_config(root, skills_dir, catalog_dir)
        payload = {
            "cyt_skills": [],
            "cyt_skill_directories": [str(skills_dir.resolve())],
        }

        clear_registry_cache()
        entries = build_registry_for_hook_payload(config, payload)
        assert entries == []


def test_build_registry_for_hook_payload_empty_without_client_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        isolate_user_home(monkeypatch, root / "home")
        skills_dir = root / "skills"
        catalog_dir = root / "catalog"
        skills_dir.mkdir()
        catalog_dir.mkdir()
        _write_skill(
            skills_dir / "from-config.md",
            "---\nname: from-config\ndescription: config\n---\n\nBody\n",
        )
        config = _skills_config(root, skills_dir, catalog_dir)

        clear_registry_cache()
        entries = build_registry_for_hook_payload(config, {"prompt": "hello"})
        assert entries == []


def test_build_registry_for_hook_payload_isolates_workspaces_in_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        isolate_user_home(monkeypatch, root / "home")
        workspace_a = root / "workspace-a"
        workspace_b = root / "workspace-b"
        catalog_dir = root / "catalog"
        workspace_a.mkdir()
        workspace_b.mkdir()
        catalog_dir.mkdir()

        skill_a_path = workspace_a / "skill-a.md"
        skill_b_path = workspace_b / "skill-b.md"
        body_a = "---\nname: skill-a\ndescription: a\n---\n\nBody A\n"
        body_b = "---\nname: skill-b\ndescription: b\n---\n\nBody B\n"
        _write_skill(skill_a_path, body_a)
        _write_skill(skill_b_path, body_b)

        base_config = {
            "cache": {"skills_dir": str(catalog_dir)},
            "skills": {
                "enabled": True,
                "pipeline": "bm25",
                "catalog_dir": str(catalog_dir),
                "directories": [],
            },
            "agents": isolated_skills_agents_block(),
        }
        config_a = set_hook_workspace_in_config(base_config, workspace_a)
        config_b = set_hook_workspace_in_config(base_config, workspace_b)
        payload_a = {
            "cyt_skills": [{"path": str(skill_a_path.resolve()), "content": body_a}],
        }
        payload_b = {
            "cyt_skills": [{"path": str(skill_b_path.resolve()), "content": body_b}],
        }

        clear_registry_cache()
        entries_a = build_registry_for_hook_payload(config_a, payload_a)
        entries_b = build_registry_for_hook_payload(config_b, payload_b)
        entries_a_again = build_registry_for_hook_payload(config_a, payload_a)

        assert {entry.doc_id for entry in entries_a} == {"skill-a"}
        assert {entry.doc_id for entry in entries_b} == {"skill-b"}
        assert {entry.doc_id for entry in entries_a_again} == {"skill-a"}
