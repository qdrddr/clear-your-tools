"""Tests for shared proxy pruning debug helpers (Anthropic & OpenAI)."""

from __future__ import annotations

from typing import Any

from cyt.proxy.anthropic import PruneResult
from cyt.proxy.openai_responses import _combine_prune_results
from cyt.proxy.pruning_debug import (
    format_removed_chunks_lines,
    merge_decomposed_catalog_snapshots,
)


def test_merge_decomposed_catalog_snapshots_suffixes_second_pass() -> None:
    first: dict[str, dict[str, Any]] = {
        "build_index": {"json": [{"file_path": "schemas/decomposed/a.json"}], "md": []},
        "rerank": {"json": [{"file_path": "schemas/decomposed/a.json"}], "md": []},
        "rerank_pruned": {"json": [{"file_path": "schemas/decomposed/a.json"}], "md": []},
    }
    second: dict[str, dict[str, Any]] = {
        "build_index": {"json": [{"file_path": "schemas/decomposed/b.json"}], "md": []},
        "rerank": {"json": [{"file_path": "schemas/decomposed/b.json"}], "md": []},
        "rerank_pruned": {"json": [], "md": []},
    }
    merged = merge_decomposed_catalog_snapshots(first, second)
    assert merged is not None
    assert "rerank#2" in merged
    assert "rerank_pruned#2" in merged
    assert merged["rerank_pruned#2"]["json"] == []


def test_combine_prune_results_merges_decomposed_catalog() -> None:
    existing = PruneResult(
        tools=None,
        status="applied",
        query="q",
        tools_in=1,
        mcp_tools_in=0,
        tools_out=1,
        error=None,
        decomposed_catalog={
            "rerank": {"json": [{"file_path": "schemas/decomposed/a.json"}], "md": []},
            "rerank_pruned": {"json": [{"file_path": "schemas/decomposed/a.json"}], "md": []},
        },
    )
    new = PruneResult(
        tools=None,
        status="applied",
        query="q",
        tools_in=1,
        mcp_tools_in=0,
        tools_out=0,
        error=None,
        decomposed_catalog={
            "rerank": {"json": [{"file_path": "schemas/decomposed/b.json"}], "md": []},
            "rerank_pruned": {"json": [], "md": []},
        },
    )
    combined = _combine_prune_results(existing, new)
    assert combined is not None
    assert combined.decomposed_catalog is not None
    assert "rerank#2" in combined.decomposed_catalog
    lines = format_removed_chunks_lines(
        {"decomposed_catalog": combined.decomposed_catalog},
    )
    text = "\n".join(lines)
    assert "rerank#2 pruned away" in text
    assert "schemas/decomposed/b.json" in text
