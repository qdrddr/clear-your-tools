"""Tests for rerank skill-node pruning."""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from cyt.common.token_usage import StageTokenUsage, empty_usage
from cyt.pruners.remote import RerankPruningSettings
from cyt.pruners.rerank import rerank_unified_item_lists
from cyt.skills.catalog import _iter_content_node_ids, build_registry
from cyt.skills.nodes import build_skill_node_items
from cyt.skills.rerank import (
    reconstruct_skills_from_reranked_items,
    rerank_skill_nodes,
)


def _write_skill(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _skills_config(root: Path, *, pipeline: str = "rerank") -> dict:
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
            "pageindex": {"enable_bm25_chunking": False},
        },
        "pruning": {
            "bm25": {"score_skills": 0.0},
            "rerank": {"score_skills": 0.0},
        },
    }


def test_build_skill_node_items_skips_frontmatter_and_uses_nodes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _skills_config(root)
        entries = build_registry(config)
        assert entries

        items = build_skill_node_items(entries)
        assert items
        assert all(item["node_id"] != 0 for item in items)
        assert all("content" in item for item in items)
        assert all("chunks" not in str(item) for item in items)


def test_reconstruct_skills_from_reranked_items_uses_node_specs() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _skills_config(root)
        entries = build_registry(config)
        node_items = build_skill_node_items(entries)
        assert node_items

        survivor = dict(node_items[0])
        survivor["score"] = "0.50000000000000000000"

        with patch(
            "cyt.skills.rerank.reconstruct_skill_markdown",
        ) as reconstruct_mock:
            reconstruct_mock.return_value = {"markdown": "# Hook\n\nBody"}
            matches = reconstruct_skills_from_reranked_items(
                [survivor],
                entries,
                config=config,
            )

        reconstruct_mock.assert_called_once()
        call_kwargs = reconstruct_mock.call_args.kwargs
        assert call_kwargs["node_id_specs"] == [str(survivor["node_id"])]
        assert call_kwargs.get("chunk_id_specs") is None
        assert matches
        assert matches[0].name == "create-hook"


def test_rerank_skill_nodes_wires_rerank_items_and_reconstruct() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _skills_config(root)
        entries = build_registry(config)
        node_items = build_skill_node_items(entries)
        assert node_items

        scored = []
        for item in node_items:
            scored_item = dict(item)
            scored_item["score"] = "0.50000000000000000000"
            scored.append(scored_item)

        with patch("cyt.skills.rerank.rerank_pruning_settings") as settings_mock:
            settings_mock.return_value = object()
            with patch(
                "cyt.skills.rerank.rerank_items",
                return_value=(scored, empty_usage()),
            ) as rerank_mock:
                with patch(
                    "cyt.skills.rerank.reconstruct_skills_from_reranked_items",
                    return_value=[],
                ) as reconstruct_mock:
                    matches, _usage = rerank_skill_nodes("agent hooks", entries, config=config)

                    rerank_mock.assert_called_once()
                    reconstruct_mock.assert_called_once()
                    assert matches == []


def test_iter_content_node_ids_skips_frontmatter() -> None:
    structure = [
        {"node_id": 0, "kind": "frontmatter"},
        {"node_id": 1, "nodes": [{"node_id": 2}]},
    ]
    assert _iter_content_node_ids(structure) == [1, 2]


def test_rerank_unified_item_lists_maps_scores_after_shadow_sort() -> None:
    item_a: dict[str, Any] = {"score": f"{0.0:.20f}"}
    item_b: dict[str, Any] = {"score": f"{0.0:.20f}"}
    item_c: dict[str, Any] = {"score": f"{0.0:.20f}"}
    targets: list[tuple[list[dict[str, Any]], Callable[[dict[str, Any]], str | None]]] = [
        ([item_a], lambda _item: "doc a"),
        ([item_b], lambda _item: "doc b"),
        ([item_c], lambda _item: "doc c"),
    ]

    def fake_rerank_prepared_bulks(
        indexed_docs: list[tuple[int, str]],
        *,
        query: str,
        settings: RerankPruningSettings,
        items: list[dict[str, Any]],
        base_tokens: int,
        min_score: float | None,
    ) -> tuple[list[dict[str, Any]], StageTokenUsage]:
        del indexed_docs, query, settings, base_tokens, min_score
        items[0]["score"] = f"{0.1:.20f}"
        items[1]["score"] = f"{0.9:.20f}"
        items[2]["score"] = f"{0.5:.20f}"
        items.sort(key=lambda row: float(str(row.get("score", 0))), reverse=True)
        return items, empty_usage()

    settings = cast(RerankPruningSettings, object())
    with patch(
        "cyt.pruners.rerank._rerank_prepared_bulks",
        side_effect=fake_rerank_prepared_bulks,
    ):
        rerank_unified_item_lists("query", targets, settings=settings)

    assert item_a["score"] == f"{0.1:.20f}"
    assert item_b["score"] == f"{0.9:.20f}"
    assert item_c["score"] == f"{0.5:.20f}"
