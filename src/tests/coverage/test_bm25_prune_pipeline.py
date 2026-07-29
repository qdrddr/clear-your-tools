"""Broad BM25 build → score → prune → diff paths for pytest-cov."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from cyt.indexer.build import build_catalog_index
from cyt.indexer.retrieve import removed_chunks, retrieve_tools
from cyt.pruners.bm25 import bm25_catalog_dict
from cyt.pruners.policies import policy_context_from_config


def _schema(description: str) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": description},
        },
        "required": ["path"],
    }


def _tool(name: str, description: str) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "input_schema": _schema(description),
    }


def _bm25_config(tmp_path: Path) -> dict[str, Any]:
    index_dir = str(tmp_path / "bm25")
    return {
        "models": {
            "bm25": {
                "index_dir": index_dir,
                "mmap": False,
                "stem_language": "english",
                "stopwords": "en",
            },
        },
        "pruning": {
            "tools": {
                "sequence": ["bm25"],
                "policy": {"per_tool": {}, "minimum_tools": 1},
                "pipelines": {"bm25": {"index_dir": index_dir}},
            },
        },
    }


def test_build_score_prune_and_diff_removed_chunks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise build → BM25 score/prune → resolve → removed_chunks."""
    monkeypatch.setenv("HOME", str(tmp_path))
    tools = [
        _tool("read_file", "Read files from disk path and return their contents."),
        _tool("write_file", "Write bytes to unrelated finance spreadsheet cells."),
        _tool("web_search", "Search the public web for recent news and documentation."),
    ]
    index = build_catalog_index(
        [
            {
                "id": t["name"],
                "server": "test",
                "tool": t["name"],
                "summary": t["description"],
                "full_schema": {
                    "id": t["name"],
                    "name": t["name"],
                    "description": t["description"],
                    "inputSchema": t["input_schema"],
                },
            }
            for t in tools
        ],
        [],
    )
    build_catalog = index.to_catalog_dict()
    catalog_data = copy.deepcopy(build_catalog)
    config = _bm25_config(tmp_path)

    scored, _usage = bm25_catalog_dict(
        catalog_data,
        "read files from disk",
        prune=True,
        config=config,
    )
    json_entries = scored.get("json") or []
    assert json_entries, "expected surviving json chunks after BM25 prune"

    read_paths = [
        entry["file_path"]
        for entry in json_entries
        if "read" in str(entry.get("file_path", "")).lower()
    ]
    assert read_paths, "read tool chunk should survive BM25 prune"

    full_catalog = copy.deepcopy(build_catalog)
    removed = removed_chunks(full_catalog, scored)
    if len(json_entries) < len(full_catalog.get("json") or []):
        assert removed.get("json"), "pruned chunks should appear in removed_chunks diff"

    ranked, _usage = bm25_catalog_dict(
        copy.deepcopy(build_catalog),
        "read files from disk",
        prune=False,
        config=config,
    )
    scores_by_path = {
        str(entry.get("file_path", "")): float(entry.get("score", 0))
        for entry in ranked.get("json") or []
    }
    read_score = next(
        (score for path, score in scores_by_path.items() if "read" in path.lower()),
        None,
    )
    write_score = next(
        (score for path, score in scores_by_path.items() if "write" in path.lower()),
        None,
    )
    assert read_score is not None and write_score is not None
    assert read_score > write_score


def test_bm25_prune_then_retrieve_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Score/prune catalog survivors and recompose merged tool schemas."""
    monkeypatch.setenv("HOME", str(tmp_path))
    tools = [
        {
            "id": "mcp__test__read",
            "server": "test",
            "tool": "read",
            "summary": "Read files from disk",
            "full_schema": {
                "id": "mcp__test__read",
                "name": "mcp__test__read",
                "description": "Read files from disk path and return their contents.",
                "inputSchema": _schema("Read files from disk path"),
            },
        },
        {
            "id": "mcp__test__write",
            "server": "test",
            "tool": "write",
            "summary": "Write unrelated data",
            "full_schema": {
                "id": "mcp__test__write",
                "name": "mcp__test__write",
                "description": "Write bytes to unrelated finance spreadsheet cells.",
                "inputSchema": _schema("Write bytes to unrelated finance spreadsheet cells."),
            },
        },
    ]
    index = build_catalog_index(tools, [])
    catalog_data = copy.deepcopy(index.to_catalog_dict())
    config = _bm25_config(tmp_path)

    scored, _usage = bm25_catalog_dict(
        catalog_data,
        "read files from disk",
        prune=True,
        config=config,
    )
    ctx = policy_context_from_config(system="prune_optional", mcp="prune_all")
    recomposed = retrieve_tools(scored, catalog=index, ctx=ctx)
    assert recomposed, "retrieve_tools should recompose pruned survivors"
    names = {tool.get("name") for tool in recomposed}
    assert any(name and "read" in name.lower() for name in names)
