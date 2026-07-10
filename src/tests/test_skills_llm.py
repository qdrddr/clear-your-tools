"""Tests for LLM skill-node pruning."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

from cyt.common.token_usage import empty_usage
from cyt.skills.catalog import _iter_content_node_ids, build_registry
from cyt.skills.llm import (
    SkillNodeMeta,
    llm_skill_nodes,
    prepare_skill_nodes,
    reconstruct_skills_from_llm_ids,
)


def _write_skill(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _skills_config(root: Path, *, pipeline: str = "llm") -> dict:
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
            "inject_via": "proxy",
            "pipeline": pipeline,
            "catalog_dir": str(catalog_dir),
            "directories": [str(skills_dir)],
            "max_tokens_per_request": 4000,
            "pageindex": {"enable_bm25_chunking": True},
        },
        "pruning": {"tools": {"pipelines": {"bm25": {"score_skills": 0.0}}}},
    }


def test_iter_content_node_ids_skips_frontmatter() -> None:
    structure = [
        {"node_id": 0, "kind": "frontmatter"},
        {"node_id": 1, "nodes": [{"node_id": 2}]},
    ]
    assert _iter_content_node_ids(structure) == [1, 2]


def test_prepare_skill_nodes_includes_token_attrs() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _skills_config(root)
        entries = build_registry(config)
        assert entries

        with patch(
            "cyt.skills.llm.load_node_content",
            side_effect=lambda _entry, node_id: (f"body-{node_id}", node_id * 10),
        ):
            formatted, metadata, item_token_counts = prepare_skill_nodes(entries)

        assert formatted
        assert item_token_counts
        assert len(item_token_counts) == len(formatted)
        combined = "\n".join(formatted)
        assert "total-tokens=" in combined
        assert " tokens=" in combined
        assert all(isinstance(meta, SkillNodeMeta) for meta in metadata.values())


def test_prepare_skill_nodes_xml_and_metadata() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _skills_config(root)
        entries = build_registry(config)
        assert entries

        formatted, metadata, item_token_counts = prepare_skill_nodes(entries)
        assert formatted
        assert item_token_counts
        assert len(item_token_counts) == len(formatted)
        combined = "\n".join(formatted)
        assert "<agent-skills total-tokens=" in combined
        assert " tokens=" in combined
        assert '<skill Path="' in combined
        assert 'name="create-hook"' in combined
        assert "<skill-node id=" in combined
        assert 'id="' not in combined.split("<skill-node", 1)[-1].split(">", 1)[0]
        assert all(isinstance(meta, SkillNodeMeta) for meta in metadata.values())
        assert all(meta.node_id != 0 for meta in metadata.values())


def test_reconstruct_skills_from_llm_ids_uses_node_specs() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _skills_config(root)
        entries = build_registry(config)
        _, metadata, _item_token_counts = prepare_skill_nodes(entries)
        assert metadata

        selector_id = next(iter(metadata))
        meta = metadata[selector_id]

        with patch(
            "cyt.skills.reconstruct.batch_reconstruct_skill_matches",
        ) as reconstruct_mock:
            reconstruct_mock.return_value = [
                {
                    "doc_id": meta.doc_id,
                    "file_path": meta.file_path,
                    "markdown": "# Hook\n\nBody",
                    "name": "create-hook",
                    "score": 1.0,
                },
            ]
            matches = reconstruct_skills_from_llm_ids(
                metadata,
                {selector_id},
                entries,
                config=config,
            )

        reconstruct_mock.assert_called_once()
        call_args = reconstruct_mock.call_args.args[0]
        assert call_args[0]["id_specs"] == [str(meta.node_id)]
        assert call_args[0]["item_kind"] == "node"
        assert matches
        assert matches[0].name == "create-hook"


def test_llm_skill_nodes_uses_pydantic_selector_ids() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _skills_config(root)
        entries = build_registry(config)
        _, metadata, item_token_counts = prepare_skill_nodes(entries)
        selector_ids = list(metadata.keys())

        enriched_query = "User_Asks: agent hooks; Assistant_Says: continue with nodes"
        with patch(
            "cyt.skills.llm.llm_select_ids",
            return_value=(set(selector_ids), empty_usage()),
        ) as select_mock:
            with patch(
                "cyt.skills.llm.reconstruct_skills_from_llm_ids",
                return_value=[],
            ):
                matches, _usage = llm_skill_nodes(enriched_query, entries, config=config)

        select_mock.assert_called_once()
        assert select_mock.call_args.args[0] == enriched_query
        assert select_mock.call_args.kwargs["chunk_token_counts"] == item_token_counts
        assert select_mock.call_args.kwargs["soft_budget_total"] == 5000
        assert matches == []
