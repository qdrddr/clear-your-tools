"""Tests for hook skills supplied by cyt-client."""

from __future__ import annotations

import tempfile
from pathlib import Path

from cyt.skills.catalog import build_registry, clear_registry_cache
from cyt.skills.client_skills import build_registry_for_hook_payload, client_skills_from_payload


def _write_skill(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _skills_config(root: Path, skills_dir: Path, catalog_dir: Path) -> dict:
    return {
        "cache": {"skills_dir": str(catalog_dir)},
        "skills": {
            "enabled": True,
            "pipeline": "bm25",
            "directories": [str(skills_dir)],
        },
    }


def test_client_skills_from_payload_requires_key() -> None:
    assert client_skills_from_payload({}) is None
    assert client_skills_from_payload({"cyt_skills": []}) == []


def test_build_registry_for_hook_payload_uses_client_skills_only() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
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
        assert not (catalog_dir / "client_staging").exists()
        assert entries[0].source_path == str(client_path.resolve())

        clear_registry_cache()
        config_entries = build_registry(config)
        assert {entry.doc_id for entry in config_entries} == {"config-only"}


def test_build_registry_for_hook_payload_without_client_uses_config_dirs() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
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
        assert {entry.doc_id for entry in entries} == {"from-config"}
