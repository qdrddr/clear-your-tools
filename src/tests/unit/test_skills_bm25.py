"""Tests for BM25 skill-chunk pruning."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from cyt.skills.bm25 import bm25_skill_chunks
from cyt.skills.catalog import build_registry
from cyt.skills.transcript import skills_search_query_from_hook_payload
from tests.support.skills_helpers import isolated_skills_agents_block


def _write_skill(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _skills_config(root: Path) -> dict:
    skills_dir = root / "skills"
    catalog_dir = root / "catalog"
    _write_skill(
        skills_dir / "create-hook.md",
        "---\nname: create-hook\ndescription: Agent hooks for Claude Code.\n---\n"
        "# Create Hook\n\nAgent hooks for Claude Code.\n\n## Usage\n\nSubmit prompts with hooks.\n",
    )
    return {
        "skills": {
            "enabled": True,
            "pipeline": "bm25",
            "catalog_dir": str(catalog_dir),
            "directories": [str(skills_dir)],
            "max_tokens_per_request": 4000,
            "pageindex": {"enable_bm25_chunking": True},
        },
        "pruning": {"tools": {"pipelines": {"bm25": {"score_skills": 0.0}}}},
        "agents": isolated_skills_agents_block(),
    }


def test_bm25_skill_chunks_returns_matches() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _skills_config(root)
        entries = build_registry(config)
        matches, usage = bm25_skill_chunks("agent hooks prompt submit", entries, config=config)
        assert usage.model_name is None or isinstance(usage.model_name, str)
        assert matches
        assert matches[0].name == "create-hook"


def test_reconstruct_omits_preamble_when_chunk_one_not_selected() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _skills_config(root)
        skills_dir = root / "skills"
        _write_skill(
            skills_dir / "ctx.md",
            "---\nname: ctx\n---\n\nIntro line\n\n# Root\n\n## Child\n\nBody text\n",
        )
        entries = build_registry(config)
        entry = next(entry for entry in entries if entry.doc_id.endswith("ctx"))
        survivors = [
            {
                "id": "3",
                "doc_id": entry.doc_id,
                "entry_dir": entry.entry_dir,
                "file_path": "ctx.md",
                "score": 0.9,
            },
        ]
        from cyt.skills.reconstruct import reconstruct_matches_from_survivor_dicts

        matches = reconstruct_matches_from_survivor_dicts(
            survivors,
            entries,
            item_kind="chunk",
            id_field="id",
        )
        assert matches
        assert "Intro line" not in matches[0].markdown
        assert "Body text" in matches[0].markdown


def test_bm25_skill_chunks_wires_native_search() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _skills_config(root)
        entries = build_registry(config)

        with patch(
            "cyt.skills.bm25.bm25_search_skill_chunks",
            return_value={"matches": [], "trace_rows": [], "threshold": 0.0},
        ) as bm25_mock:
            matches, _usage = bm25_skill_chunks("agent hooks", entries, config=config)

        bm25_mock.assert_called_once()
        assert matches == []


def test_node_reconstruct_batch_matches_direct_node_id_specs() -> None:
    """Regression: batch reconstruct must use node_id_specs, not line_num_specs."""
    from cyt.skills.frontmatter import injection_markdown_body
    from cyt.skills.reconstruct import reconstruct_matches_from_survivor_dicts
    from tests.integration.test_llm_prune_integration import (
        DEFAULT_SKILL_ENTRY_DIR,
        DEFAULT_SKILL_NODE_ID,
        load_single_skill_entry,
    )

    if not DEFAULT_SKILL_ENTRY_DIR.is_dir():
        import pytest

        pytest.skip("lean-ctx skill cache fixture unavailable")

    entry = load_single_skill_entry(DEFAULT_SKILL_ENTRY_DIR, node_id=DEFAULT_SKILL_NODE_ID)
    survivors = [
        {
            "entry_dir": entry.entry_dir,
            "doc_id": entry.doc_id,
            "node_id": DEFAULT_SKILL_NODE_ID,
            "file_path": entry.source_path,
            "score": 1.0,
        },
    ]
    matches = reconstruct_matches_from_survivor_dicts(
        survivors,
        [entry],
        item_kind="node",
        id_field="node_id",
    )
    assert matches
    body = injection_markdown_body(matches[0].markdown)
    assert body.strip()
    assert "File Editing" in body


def test_transcript_enriched_query_improves_bm25_match() -> None:
    """Assistant context from transcript_path should reach BM25 as format_search_query."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        skills_dir = root / "skills"
        catalog_dir = root / "catalog"
        transcript = root / "session.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "Clear Your Tools prunes agent tools via BM25 rerank and LLM.",
                            },
                        ],
                        "phase": "final_answer",
                    },
                },
            ),
            encoding="utf-8",
        )
        _write_skill(
            skills_dir / "create-hook.md",
            "---\nname: create-hook\ndescription: Agent hooks for Claude Code.\n---\n"
            "# Create Hook\n\nAgent hooks for Claude Code sessions.\n",
        )
        config = {
            "skills": {
                "catalog_dir": str(catalog_dir),
                "directories": [str(skills_dir)],
                "max_tokens_per_request": 4000,
                "pageindex": {"enable_bm25_chunking": True},
            },
            "pruning": {"tools": {"pipelines": {"bm25": {"score_skills": 0.0}}}},
            "agents": isolated_skills_agents_block(),
        }
        entries = build_registry(config)

        enriched_query = skills_search_query_from_hook_payload(
            {
                "prompt": "continue",
                "transcript_path": str(transcript),
            },
        )
        assert enriched_query == (
            "User_Asks: continue; Assistant_Says: "
            "Clear Your Tools prunes agent tools via BM25 rerank and LLM."
        )

        with patch(
            "cyt.skills.bm25.bm25_search_skill_chunks",
            wraps=__import__(
                "cyt_indexer.bm25_search",
                fromlist=["bm25_search_skill_chunks"],
            ).bm25_search_skill_chunks,
        ) as bm25_mock:
            matches, _ = bm25_skill_chunks(enriched_query or "", entries, config=config)

        assert bm25_mock.call_count == 1
        assert "Assistant_Says:" in bm25_mock.call_args.args[1]
        assert matches
        assert matches[0].name == "create-hook"
