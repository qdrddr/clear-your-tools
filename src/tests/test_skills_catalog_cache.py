"""Tests for per-skill catalog cache keys and states."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from cyt.config import skills_index_params_fingerprint
from cyt.skills.catalog import (
    SkillEntryRef,
    _shorten_home_path,
    build_registry,
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
        assert entry.bm25_chunk_dir.endswith(f"chunks/bm25/{params_hash}")
        doc = json.loads((Path(entry.nodes_dir) / "page_index.json").read_text())
        assert doc["content_sha256"] == content_sha256_for_file(skills_dir / "create-hook.md")
        assert doc["pipeline"] == "bm25"
        skill_path = skills_dir / "create-hook.md"
        assert doc["path"] == _shorten_home_path(str(skill_path))
        assert doc["path"].endswith("create-hook.md")
        assert entry.disk_backed is True
