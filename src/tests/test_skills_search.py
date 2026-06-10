"""Tests for BM25 skills search."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

from cyt.skills.catalog import build_registry
from cyt.skills.search import search_skills


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
            "pruning": {"bm25": {"score_skills": 0.0}},
        }
        entries = build_registry(config)
        matches = search_skills("agent hooks prompt submit", entries, config=config)
        assert matches
        assert any("hook" in match.markdown.lower() for match in matches)


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
            "pruning": {"bm25": {"score_skills": 0.99}},
        }
        entries = build_registry(config)
        with patch("cyt.skills.search.bm25_score_skills", return_value=0.99):
            matches = search_skills("quantum physics rockets", entries, config=config)
        assert matches == []
