"""Tests for BM25 skill-chunk pruning."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from cyt.common.token_usage import empty_usage
from cyt.pruners.bm25 import bm25_catalog_dict
from cyt.skills.bm25 import (
    bm25_skill_chunks,
    reconstruct_skills_from_bm25_items,
)
from cyt.skills.catalog import build_registry
from cyt.skills.transcript import skills_search_query_from_hook_payload


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
        "pruning": {"bm25": {"score_skills": 0.0}},
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


def test_reconstruct_skills_from_bm25_items_uses_chunk_specs() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _skills_config(root)
        entries = build_registry(config)
        survivor = {
            "id": "2",
            "doc_id": entries[0].doc_id,
            "entry_dir": entries[0].entry_dir,
            "file_path": "create-hook.md",
            "score": 0.42,
        }

        with patch(
            "cyt.skills.bm25.reconstruct_skill_markdown",
            return_value={"markdown": "# Create Hook\n\nBody\n"},
        ) as reconstruct_mock:
            matches = reconstruct_skills_from_bm25_items([survivor], entries, config=config)

        reconstruct_mock.assert_called_once()
        call_kwargs = reconstruct_mock.call_args.kwargs
        assert call_kwargs["chunk_id_specs"] == ["2"]
        assert call_kwargs.get("node_id_specs") is None
        assert matches
        assert matches[0].name == "create-hook"


def test_bm25_skill_chunks_wires_bm25_catalog_dict() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _skills_config(root)
        entries = build_registry(config)

        with patch(
            "cyt.skills.bm25.bm25_catalog_dict",
            return_value=({"md": []}, empty_usage()),
        ) as bm25_mock:
            matches, _usage = bm25_skill_chunks("agent hooks", entries, config=config)

        bm25_mock.assert_called_once()
        assert matches == []


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
            "pruning": {"bm25": {"score_skills": 0.0}},
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
            "cyt.skills.bm25.bm25_catalog_dict",
            wraps=bm25_catalog_dict,
        ) as bm25_mock:
            matches, _ = bm25_skill_chunks(enriched_query or "", entries, config=config)

        assert bm25_mock.call_count == 1
        assert "Assistant_Says:" in bm25_mock.call_args.args[1]
        assert matches
        assert matches[0].name == "create-hook"
