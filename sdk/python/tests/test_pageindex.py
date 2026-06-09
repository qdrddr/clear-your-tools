"""Tests for skills pageindex bindings."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

cyt_indexer = pytest.importorskip("cyt_indexer")

from cyt_indexer import (  # noqa: E402
    PageIndexConfig,
    SkillsBuilder,
    build_skills_index,
    get_skill_document,
    get_skill_page_content,
    get_skill_structure,
    md_to_tree,
    skills_index_from_decomposed_dir,
    write_skills_index,
)


def test_md_to_tree_in_memory() -> None:
    md = "# Title\n\nBody\n\n## Sub\n\nMore"
    result = md_to_tree(md, "skill.md", config=PageIndexConfig())
    assert result["doc_name"] == "skill"
    assert result["line_count"] >= 1
    assert isinstance(result["structure"], list)


def test_build_write_reconstruct() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        skills_dir = Path(tmp) / "skills"
        skills_dir.mkdir()
        (skills_dir / "demo.md").write_text("# Demo\n\nHello\n\n## Part\n\nWorld", encoding="utf-8")

        index = build_skills_index([str(skills_dir)])
        assert "documents" in index
        assert index["documents"]

        catalog = Path(tmp) / "catalog"
        write_skills_index(index, str(catalog))
        snapshot = catalog / "skills_index.json"
        assert snapshot.is_file()
        snapshot.unlink()

        rebuilt = skills_index_from_decomposed_dir(str(catalog))
        doc_id = next(iter(rebuilt["documents"]))
        meta = get_skill_document(rebuilt["documents"], doc_id)
        assert meta["type"] == "md"
        structure = get_skill_structure(rebuilt["documents"], doc_id)
        assert structure
        content = get_skill_page_content(rebuilt, doc_id, "1")
        assert content


def test_skills_builder_memory_only() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        skills_dir = Path(tmp) / "skills"
        skills_dir.mkdir()
        (skills_dir / "x.md").write_text("# X\n\nY", encoding="utf-8")

        builder = SkillsBuilder(memory_only=True)
        index = builder.build_from_dirs([str(skills_dir)])
        assert index["files"]
