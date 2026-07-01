"""Tests for BM25 skills search."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

from cyt.common.token_usage import empty_usage
from cyt.skills.catalog import _iter_chunk_ids, _iter_content_chunk_ids, build_registry
from cyt.skills.search import search_skills, search_skills_with_pipeline


def _write_skill(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_search_returns_matches_for_relevant_query() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skills_dir = root / "skills"
        catalog_dir = root / "catalog"
        _write_skill(
            skills_dir / "create-hook.md",
            "---\nname: create-hook\ndescription: Agent hooks for Claude Code.\n---\n"
            "# Create Hook\n\nAgent hooks for Claude Code.\n\n## Usage\n\nSubmit prompts with hooks.\n",
        )
        config = {
            "skills": {
                "enabled": True,
                "pipeline": "bm25",
                "catalog_dir": str(catalog_dir),
                "directories": [str(skills_dir)],
                "max_tokens_per_request": 4000,
                "pageindex": {"enable_bm25_chunking": True},
            },
            "pruning": {"tools": {"pipelines": {"bm25": {"score_skills": 0.0}}}},
        }
        entries = build_registry(config)
        matches = search_skills("agent hooks prompt submit", entries, config=config)
        assert matches
        assert any(match.name == "create-hook" for match in matches)
        assert all(m.file_path.endswith("create-hook.md") for m in matches)


def test_search_empty_query_returns_no_matches() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skills_dir = root / "skills"
        catalog_dir = root / "catalog"
        _write_skill(skills_dir / "skill.md", "# Skill\n\nBody\n")
        config = {
            "skills": {
                "catalog_dir": str(catalog_dir),
                "directories": [str(skills_dir)],
                "pageindex": {"enable_bm25_chunking": True},
            },
        }
        entries = build_registry(config)
        assert search_skills("", entries, config=config) == []
        assert search_skills("   ", entries, config=config) == []


def test_search_below_threshold_filters_matches() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skills_dir = root / "skills"
        catalog_dir = root / "catalog"
        _write_skill(
            skills_dir / "unrelated.md",
            "# Zebra\n\nCompletely unrelated topic about databases only.\n",
        )
        config = {
            "skills": {
                "catalog_dir": str(catalog_dir),
                "directories": [str(skills_dir)],
                "pageindex": {"enable_bm25_chunking": True},
            },
            "pruning": {"tools": {"pipelines": {"bm25": {"score_skills": 0.99}}}},
        }
        entries = build_registry(config)
        with patch("cyt.skills.bm25.bm25_score_skills", return_value=0.99):
            matches = search_skills("quantum physics rockets", entries, config=config)
        assert matches == []


def test_frontmatter_gate_excludes_similar_skill() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skills_dir = root / "skills"
        catalog_dir = root / "catalog"
        _write_skill(
            skills_dir / "create-hook.md",
            "---\nname: create-hook\ndescription: Agent hooks for Claude Code.\n---\n"
            "# Create Hook\n\nAgent hooks for Claude Code.\n\n## Usage\n\nSubmit prompts with hooks.\n",
        )
        config = {
            "skills": {
                "catalog_dir": str(catalog_dir),
                "directories": [str(skills_dir)],
                "frontmatter_upper_limit": 0.0,
                "max_tokens_per_request": 4000,
                "pageindex": {"enable_bm25_chunking": True},
            },
            "pruning": {"tools": {"pipelines": {"bm25": {"score_skills": 0.0}}}},
        }
        entries = build_registry(config)
        matches = search_skills(
            "create-hook agent hooks for claude code",
            entries,
            config=config,
        )
        assert matches == []


def test_frontmatter_gate_allows_dissimilar_skill() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skills_dir = root / "skills"
        catalog_dir = root / "catalog"
        _write_skill(
            skills_dir / "create-hook.md",
            "---\nname: create-hook\ndescription: Agent hooks for Claude Code.\n---\n"
            "# Zebra Migration\n\nDatabase shard rebalancing procedures.\n",
        )
        config = {
            "skills": {
                "catalog_dir": str(catalog_dir),
                "directories": [str(skills_dir)],
                "frontmatter_upper_limit": 0.4,
                "max_tokens_per_request": 4000,
                "pageindex": {"enable_bm25_chunking": True},
            },
            "pruning": {"tools": {"pipelines": {"bm25": {"score_skills": 0.0}}}},
        }
        entries = build_registry(config)
        matches = search_skills(
            "database shard rebalancing zebra migration",
            entries,
            config=config,
        )
        assert matches
        assert matches[0].name == "create-hook"


def test_frontmatter_gate_trace_reports_scores() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skills_dir = root / "skills"
        catalog_dir = root / "catalog"
        _write_skill(
            skills_dir / "context7.md",
            "---\nname: context7\ndescription: Use Context7 MCP for specific library documentation.\n---\n"
            "# Context7\n\nUse Context7 MCP for specific library documentation.\n",
        )
        _write_skill(
            skills_dir / "other.md",
            "---\nname: other\ndescription: Unrelated database topic.\n---\n"
            "# Other\n\nDatabase shard rebalancing only.\n",
        )
        config = {
            "skills": {
                "catalog_dir": str(catalog_dir),
                "directories": [str(skills_dir)],
                "frontmatter_upper_limit": 0.4,
                "max_tokens_per_request": 4000,
                "pageindex": {"enable_bm25_chunking": True},
            },
            "pruning": {"tools": {"pipelines": {"bm25": {"score_skills": 0.0}}}},
        }
        entries = build_registry(config)
        from cyt.skills.bm25 import frontmatter_gate_trace

        rows, _limit = frontmatter_gate_trace(
            "use context7 specific library docs",
            entries,
            config=config,
        )
        context7 = next(row for row in rows if "context7" in row.doc_id)
        assert context7.raw_score is not None
        assert context7.score is not None
        assert 0.0 <= context7.score <= 1.0
        assert context7.score > 0.0


def test_content_corpus_excludes_frontmatter_chunks() -> None:
    structure = [
        {
            "node_id": 0,
            "kind": "frontmatter",
            "chunks": [{"chunk_id": 1}],
        },
        {
            "node_id": 2,
            "title": "Usage",
            "chunks": [{"chunk_id": 2}, {"chunk_id": 3}],
        },
    ]
    assert _iter_chunk_ids(structure) == [1, 2, 3]
    assert _iter_content_chunk_ids(structure) == [2, 3]


def test_match_name_preserved_after_content_only_search() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skills_dir = root / "skills"
        catalog_dir = root / "catalog"
        _write_skill(
            skills_dir / "create-hook.md",
            "---\nname: create-hook\ndescription: Agent hooks for Claude Code.\n---\n"
            "# Zebra Migration\n\nDatabase shard rebalancing procedures.\n",
        )
        config = {
            "skills": {
                "catalog_dir": str(catalog_dir),
                "directories": [str(skills_dir)],
                "frontmatter_upper_limit": 0.4,
                "max_tokens_per_request": 4000,
                "pageindex": {"enable_bm25_chunking": True},
            },
            "pruning": {"tools": {"pipelines": {"bm25": {"score_skills": 0.0}}}},
        }
        entries = build_registry(config)
        matches = search_skills(
            "database shard rebalancing zebra migration",
            entries,
            config=config,
        )
        assert len(matches) == 1
        assert matches[0].name == "create-hook"


def test_search_rerank_pipeline_dispatches_to_rerank_skill_nodes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skills_dir = root / "skills"
        catalog_dir = root / "catalog"
        _write_skill(
            skills_dir / "create-hook.md",
            "---\nname: create-hook\ndescription: Agent hooks.\n---\n"
            "# Create Hook\n\nAgent hooks for Claude Code.\n",
        )
        config = {
            "skills": {
                "pipeline": "rerank",
                "catalog_dir": str(catalog_dir),
                "directories": [str(skills_dir)],
                "max_tokens_per_request": 4000,
                "bm25_node_fallback_threshold": 0,
                "pageindex": {"enable_bm25_chunking": False},
            },
            "pruning": {"tools": {"pipelines": {"rerank": {"score_skills": 0.0}}}},
        }
        entries = build_registry(config)
        with patch(
            "cyt.skills.rerank.rerank_skill_nodes_with_trace",
            return_value=([], [], 0.0, empty_usage()),
        ) as rerank_mock:
            search_skills("agent hooks", entries, config=config)

        rerank_mock.assert_called_once()


def test_search_llm_pipeline_dispatches_to_llm_skill_nodes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skills_dir = root / "skills"
        catalog_dir = root / "catalog"
        _write_skill(
            skills_dir / "create-hook.md",
            "---\nname: create-hook\ndescription: Agent hooks.\n---\n"
            "# Create Hook\n\nAgent hooks for Claude Code.\n",
        )
        config = {
            "skills": {
                "pipeline": "llm",
                "catalog_dir": str(catalog_dir),
                "directories": [str(skills_dir)],
                "max_tokens_per_request": 4000,
                "bm25_node_fallback_threshold": 0,
                "pageindex": {"enable_bm25_chunking": True},
            },
        }
        entries = build_registry(config)
        with patch(
            "cyt.skills.llm.llm_skill_nodes_with_trace",
            return_value=([], [], empty_usage()),
        ) as llm_mock:
            search_skills("agent hooks", entries, config=config)

        llm_mock.assert_called_once()


def test_search_with_pipeline_reports_bm25_node_fallback() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skills_dir = root / "skills"
        catalog_dir = root / "catalog"
        _write_skill(
            skills_dir / "create-hook.md",
            "---\nname: create-hook\ndescription: Agent hooks.\n---\n"
            "# Create Hook\n\nAgent hooks for Claude Code.\n",
        )
        config = {
            "skills": {
                "pipeline": "rerank",
                "catalog_dir": str(catalog_dir),
                "directories": [str(skills_dir)],
                "max_tokens_per_request": 4000,
                "bm25_node_fallback_threshold": 50,
                "pageindex": {"enable_bm25_chunking": True},
            },
            "pruning": {"tools": {"pipelines": {"bm25": {"score_skills": 0.0}}}},
        }
        entries = build_registry(config)
        _matches, pipeline_run = search_skills_with_pipeline(
            "agent hooks prompt submit",
            entries,
            config=config,
        )
        assert pipeline_run.configured == "rerank"
        assert pipeline_run.executed == "bm25"
        assert pipeline_run.fallback_reason is not None
        assert "bm25_node_fallback_threshold" in pipeline_run.fallback_reason
