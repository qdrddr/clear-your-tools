"""Tests for skills pageindex bindings."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

cyt_indexer = pytest.importorskip("cyt_indexer")

from cyt_indexer import (  # noqa: E402
    Bm25CohesionConfig,
    PageIndexConfig,
    SkillsBuilder,
    bm25_cohesion_chunk,
    build_skills_index,
    default_page_index_config,
    get_skill_document,
    get_skill_line_content,
    get_skill_line_content_from_spec,
    get_skill_structure,
    page_index_config_from_mapping,
    page_index_config_without_chunking,
    parse_skill_chunk_ids,
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


def test_default_page_index_config_includes_bm25() -> None:
    cfg = default_page_index_config()
    d = cfg.to_dict()
    assert d["bm25_cohesion"]["chunk_size"] == 2048
    assert d["bm25_cohesion"]["window_mode"] == "sentence"


def test_page_index_config_from_mapping_partial_bm25() -> None:
    cfg = page_index_config_from_mapping({"bm25_cohesion": {"skip_window": 2}})
    assert cfg["bm25_cohesion"]["skip_window"] == 2
    assert cfg["bm25_cohesion"]["chunk_size"] == 2048


def test_bm25_cohesion_chunk_standalone() -> None:
    text = "Alpha one two three. Beta finance market stocks."
    chunks = bm25_cohesion_chunk(text, Bm25CohesionConfig(chunk_size=2048))
    assert len(chunks) == 1
    assert chunks[0]["token_count"] > 0


def test_parse_skill_chunk_ids() -> None:
    assert parse_skill_chunk_ids("8") == [8]
    assert parse_skill_chunk_ids("8-10") == [8, 9, 10]


def test_build_write_reconstruct() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        skills_dir = Path(tmp) / "skills"
        skills_dir.mkdir()
        (skills_dir / "demo.md").write_text("# Demo\n\nHello\n\n## Part\n\nWorld", encoding="utf-8")

        index = build_skills_index([str(skills_dir)])
        assert "documents" in index
        assert index["documents"]
        assert any("/chunks/" in k for k in index["files"])

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
        content = get_skill_line_content_from_spec(rebuilt, doc_id, "1")
        assert content


def test_retrieve_by_chunk_id_after_disk_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        skills_dir = Path(tmp) / "skills"
        skills_dir.mkdir()
        (skills_dir / "demo.md").write_text(
            "# Demo\n\nShort.\n\n## Part\n\nLonger section with more words here.",
            encoding="utf-8",
        )
        index = build_skills_index([str(skills_dir)])
        doc_id = next(iter(index["documents"]))
        chunk_id = next(k.split("/")[-1].removesuffix(".md") for k in index["files"] if "/chunks/" in k)

        catalog = Path(tmp) / "catalog"
        write_skills_index(index, str(catalog))
        rebuilt = skills_index_from_decomposed_dir(str(catalog))
        rows = get_skill_line_content(rebuilt, doc_id, chunk_id_specs=[chunk_id])
        assert rows
        assert rows[0]["chunk_id"] == int(chunk_id)
        assert rows[0]["content"]


def test_build_without_bm25_chunking_skips_chunk_files() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        skills_dir = Path(tmp) / "skills"
        skills_dir.mkdir()
        (skills_dir / "demo.md").write_text("# Demo\n\nHello\n\n## Part\n\nWorld", encoding="utf-8")

        index = build_skills_index([str(skills_dir)], config=page_index_config_without_chunking())
        assert "documents" in index
        assert index["documents"]
        assert not any("/chunks/" in k for k in index["files"])
        assert any(k.endswith("/document.json") for k in index["files"])
        assert any(k.endswith(".md") and "/chunks/" not in k for k in index["files"])

        doc_id = next(iter(index["documents"]))
        structure = get_skill_structure(index["documents"], doc_id)
        assert "chunks" not in str(structure)


def test_skills_builder_memory_only() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        skills_dir = Path(tmp) / "skills"
        skills_dir.mkdir()
        (skills_dir / "x.md").write_text("# X\n\nY", encoding="utf-8")

        builder = SkillsBuilder(memory_only=True)
        index = builder.build_from_dirs([str(skills_dir)])
        assert index["files"]
