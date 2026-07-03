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


def test_prepare_skill_nodes_xml_and_metadata() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _skills_config(root)
        entries = build_registry(config)
        assert entries

        formatted, metadata = prepare_skill_nodes(entries)
        assert formatted
        combined = "\n".join(formatted)
        assert "<agent-skills>" in combined
        assert '<skill Path="' in combined
        assert 'name="create-hook"' in combined
        assert "<skill-node id=" in combined
        assert all(isinstance(meta, SkillNodeMeta) for meta in metadata.values())
        assert all(meta.node_id != 0 for meta in metadata.values())


def test_reconstruct_skills_from_llm_ids_uses_node_specs() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _skills_config(root)
        entries = build_registry(config)
        _, metadata = prepare_skill_nodes(entries)
        assert metadata

        selector_id = next(iter(metadata))
        meta = metadata[selector_id]

        with patch(
            "cyt.skills.reconstruct.reconstruct_skill_markdown",
        ) as reconstruct_mock:
            reconstruct_mock.return_value = {"markdown": "# Hook\n\nBody"}
            matches = reconstruct_skills_from_llm_ids(
                metadata,
                {selector_id},
                entries,
                config=config,
            )

        reconstruct_mock.assert_called_once()
        call_kwargs = reconstruct_mock.call_args.kwargs
        assert call_kwargs["node_id_specs"] == [str(meta.node_id)]
        assert call_kwargs.get("chunk_id_specs") is None
        assert matches
        assert matches[0].name == "create-hook"


def test_llm_skill_nodes_uses_pydantic_selector_ids() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _skills_config(root)
        entries = build_registry(config)
        _, metadata = prepare_skill_nodes(entries)
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
        assert matches == []
