"""Tests for per-skill catalog cache keys and states."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest

from cyt.common.agents import AgentName
from cyt.config import skills_index_params_fingerprint
from cyt.skills.catalog import (
    SkillEntryRef,
    build_registry,
    clear_registry_cache,
    content_sha256_for_file,
    doc_id_from_path,
)


def _write_skill(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_content_sha256_dedup_uses_content_hash_as_cache_key() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skill_a = root / "a" / "alpha.md"
        skill_b = root / "b" / "beta.md"
        _write_skill(skill_a, "# Alpha\n\nSearch hooks and agents.\n")
        _write_skill(skill_b, "# Alpha\n\nSearch hooks and agents.\n")

        hash_a = content_sha256_for_file(skill_a)
        hash_b = content_sha256_for_file(skill_b)
        assert hash_a == hash_b


def test_build_registry_complete_and_dedup() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skills_dir = root / "skills"
        dup_dir = root / "dup"
        catalog_dir = root / "catalog"
        _write_skill(
            skills_dir / "create-hook.md",
            "# Create Hook\n\nIntro\n\n## Usage\n\nRun hooks for agent sessions.\n",
        )
        _write_skill(
            dup_dir / "create-hook-copy.md",
            (skills_dir / "create-hook.md").read_text(encoding="utf-8"),
        )

        config = {
            "skills": {
                "enabled": True,
                "pipeline": "bm25",
                "catalog_dir": str(catalog_dir),
                "directories": [str(skills_dir), str(dup_dir)],
                "pageindex": {"enable_bm25_chunking": True},
            },
            "agents": {
                "cursor": {"skills": {"directories": []}},
                "claude": {"skills": {"directories": []}},
                "codex": {"skills": {"directories": []}},
            },
        }

        entries = build_registry(config)
        assert len(entries) == 1
        entry = entries[0]
        assert isinstance(entry, SkillEntryRef)
        assert entry.doc_id == doc_id_from_path(skills_dir / "create-hook.md")
        assert entry.cache_key == entry.content_sha256
        assert (Path(entry.nodes_dir) / "page_index.json").is_file()
        params_hash = skills_index_params_fingerprint(config)
        assert (Path(entry.bm25_chunk_dir) / "chunk_index.json").is_file()
        assert Path(entry.bm25_chunk_dir).as_posix().endswith(f"chunks/bm25/{params_hash}")
        doc = json.loads((Path(entry.nodes_dir) / "page_index.json").read_text())
        assert "content_sha256" not in doc
        assert "built_at" not in doc
        assert "pipeline" not in doc
        assert Path(doc["path"]).resolve() == (skills_dir / "create-hook.md").resolve()
        metadata = json.loads((Path(entry.entry_dir) / "metadata.json").read_text())
        assert metadata["pipeline"] == "bm25"
        assert Path(metadata["source_path"]).resolve().as_posix().endswith("skills/create-hook.md")
        chunk_index = json.loads((Path(entry.bm25_chunk_dir) / "chunk_index.json").read_text())
        assert chunk_index["pipeline"] == "bm25"
        assert "index_params" in chunk_index
        assert entry.disk_backed is True


def test_build_registry_process_cache_reuses_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skills_dir = root / "skills"
        catalog_dir = root / "catalog"
        _write_skill(
            skills_dir / "create-hook.md",
            "# Create Hook\n\nIntro\n\n## Usage\n\nRun hooks for agent sessions.\n",
        )
        config = {
            "skills": {
                "enabled": True,
                "pipeline": "bm25",
                "catalog_dir": str(catalog_dir),
                "directories": [str(skills_dir)],
                "pageindex": {"enable_bm25_chunking": True},
            },
            "agents": {
                "cursor": {"skills": {"directories": []}},
                "claude": {"skills": {"directories": []}},
                "codex": {"skills": {"directories": []}},
            },
        }
        clear_registry_cache()
        calls: list[int] = []

        from cyt.skills import catalog as catalog_mod

        original_build = catalog_mod._build_registry_uncached

        def counting_build(
            cfg: dict[str, Any],
            *,
            agent: AgentName | None = None,
            upstream_kind: str | None = None,
            client_skills: list[dict[str, str]] | None = None,
        ) -> list[SkillEntryRef]:
            calls.append(1)
            return original_build(
                cfg,
                agent=agent,
                upstream_kind=upstream_kind,
                client_skills=client_skills,
            )

        monkeypatch.setattr(catalog_mod, "_build_registry_uncached", counting_build)
        first = build_registry(config)
        second = build_registry(config)
        assert len(first) == 1
        assert first[0].doc_id == second[0].doc_id
        assert calls == [1]
        clear_registry_cache()


def test_build_registry_reflects_permission_change_without_cache_clear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skills_dir = root / "skills"
        catalog_dir = root / "catalog"
        skill_path = skills_dir / "demo-skill" / "SKILL.md"
        skill_path.parent.mkdir(parents=True)
        skill_path.write_text("---\nname: demo-skill\n---\nbody\n", encoding="utf-8")
        config = {
            "skills": {
                "enabled": True,
                "pipeline": "bm25",
                "catalog_dir": str(catalog_dir),
                "directories": [str(skills_dir)],
                "pageindex": {"enable_bm25_chunking": True},
            },
            "agents": {
                "cursor": {"skills": {"directories": []}},
                "claude": {"skills": {"directories": []}},
                "codex": {"skills": {"directories": []}},
            },
        }
        clear_registry_cache()

        from cyt.permissions.schema import EffectivePermissions, SkillsPermissions

        deny_state: dict[str, tuple[str, ...]] = {"deny": ()}

        def fake_effective(**kwargs: object) -> EffectivePermissions:
            return EffectivePermissions(
                skills=SkillsPermissions(deny=deny_state["deny"]),
            )

        monkeypatch.setattr(
            "cyt.permissions.runtime.effective_permissions",
            fake_effective,
        )
        monkeypatch.setattr(
            "cyt.permissions.merge.effective_permissions",
            fake_effective,
        )

        enabled = build_registry(config)
        assert len(enabled) == 1

        deny_state["deny"] = ("demo-skill",)
        filtered = build_registry(config)
        assert filtered == []
        clear_registry_cache()
