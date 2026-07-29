"""Precise catalog_common branch assertions (mutation killers)."""

from __future__ import annotations

import copy
from typing import Any

from cyt.pruners.catalog_common import (
    catalog_below_minimum_tools,
    finalize_catalog_result,
    finalize_scored_stage,
    prepare_catalog_for_scoring,
    prune_catalog_lists,
)
from cyt.pruners.policies import policy_context_from_config


def _catalog(*scores: tuple[str, float]) -> dict[str, Any]:
    return {
        "json": [{"file_path": path, "score": f"{score:.20f}"} for path, score in scores],
        "md": [
            {"file_path": "schemas/decomposed/format.md", "score": "0.05000000000000000000"},
        ],
    }


def test_prune_catalog_lists_keeps_json_at_or_above_threshold() -> None:
    data = _catalog(("keep.json", 0.5), ("drop.json", 0.05))
    pruned = prune_catalog_lists(
        copy.deepcopy(data),
        json_threshold=0.1,
        md_threshold=0.9,
        prune_enums=False,
    )
    paths = [item["file_path"] for item in pruned["json"]]
    assert paths == ["keep.json"]
    assert len(pruned["md"]) == 1


def test_prune_catalog_lists_drops_md_only_when_prune_enums_true() -> None:
    data = _catalog(("keep.json", 0.5))
    without_enums = prune_catalog_lists(
        copy.deepcopy(data),
        json_threshold=0.0,
        md_threshold=0.9,
        prune_enums=False,
    )
    with_enums = prune_catalog_lists(
        copy.deepcopy(data),
        json_threshold=0.0,
        md_threshold=0.9,
        prune_enums=True,
    )
    assert len(without_enums["md"]) == 1
    assert with_enums["md"] == []


def test_finalize_catalog_result_merges_pinned_only_when_requested() -> None:
    scored = {"json": [{"file_path": "scored.json", "score": "0.5"}], "md": []}
    pinned = {"json": [{"file_path": "pinned.json", "score": "1.0"}], "md": []}

    merged = finalize_catalog_result(scored, pinned, merge_pinned=True)
    paths = {item["file_path"] for item in merged["json"]}
    assert paths == {"scored.json", "pinned.json"}

    untouched = finalize_catalog_result(scored, pinned, merge_pinned=False)
    assert [item["file_path"] for item in untouched["json"]] == ["scored.json"]


def test_prepare_catalog_for_scoring_pass_through_skips_partition() -> None:
    ctx = policy_context_from_config(system="always_include", mcp="always_include")
    catalog = {
        "json": [{"file_path": "schemas/decomposed/sys.json", "content": {}}],
        "md": [],
    }
    data, pinned, skip = prepare_catalog_for_scoring(catalog, ctx)
    assert skip is True
    assert pinned == {}
    assert data is catalog


def test_finalize_scored_stage_preserves_pre_and_post_prune_snapshots() -> None:
    scored = _catalog(("high.json", 0.9), ("low.json", 0.01))

    def _prune(data: dict[str, Any]) -> dict[str, Any]:
        return prune_catalog_lists(
            data,
            json_threshold=0.5,
            md_threshold=1.0,
            prune_enums=True,
        )

    stage = finalize_scored_stage(scored, prune_fn=_prune)
    assert len(stage.post_rerank_scored["json"]) == 2
    assert [item["file_path"] for item in stage.data["json"]] == ["high.json"]
    assert stage.post_rerank == stage.data


def test_catalog_below_minimum_tools_boundary() -> None:
    catalog = {
        "json": [
            {"file_path": "schemas/decomposed/one.json", "content": {"name": "one"}},
        ],
        "md": [],
    }
    assert catalog_below_minimum_tools(catalog, 2, stage="bm25") is True
    assert catalog_below_minimum_tools(catalog, 1, stage="bm25") is False


def test_prepare_catalog_for_scoring_partitions_when_policy_requires() -> None:
    ctx = policy_context_from_config(system="prune_optional", mcp="prune_all")
    catalog = {
        "json": [
            {"file_path": "schemas/decomposed/mcp__srv__tool.json", "content": {}},
            {"file_path": "schemas/decomposed/mcp__srv__tool/extra.json", "content": {}},
        ],
        "md": [],
    }
    data, pinned, skip = prepare_catalog_for_scoring(catalog, ctx)
    assert skip is False
    assert pinned.get("json") or data.get("json")
