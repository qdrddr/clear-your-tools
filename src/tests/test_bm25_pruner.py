"""Tests for BM25 catalog pruning."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from cyt.config import effective_pruning_pipeline
from cyt.indexer.build import build_catalog_index
from cyt.proxy.anthropic import filter_tools_for_query
from cyt.pruners.bm25 import (
    BM25_SCORE,
    BM25_STATS_ID,
    bm25_catalog_dict,
    bm25_stage_usage,
    build_bm25_tokenizer,
    build_or_load_index,
    catalog_fingerprint,
    prune_bm25_catalog,
)
from cyt.pruners.documents import extract_document_text, extract_json_catalog_document
from cyt.pruners.policies import mitigate_empty_optional_properties


def _schema_with_optional() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "read_path": {
                "type": "string",
                "description": "Read files from disk path",
            },
            "write_path": {
                "type": "string",
                "description": "Write output to storage location",
            },
            "format": {
                "type": "string",
                "description": "Output format",
                "enum": ["json", "yaml"],
                "default": "json",
            },
        },
    }


def _make_tool(name: str = "mcp__test__search") -> dict[str, Any]:
    return {
        "id": name,
        "server": "test",
        "tool": name,
        "summary": "Search tool",
        "full_schema": {
            "id": name,
            "name": name,
            "description": "Search files",
            "inputSchema": _schema_with_optional(),
        },
    }


def _catalog_from_tools(tools: list[dict[str, Any]]) -> dict[str, Any]:
    return build_catalog_index(tools, []).to_catalog_dict()


def test_extract_document_text_includes_description_default_enum() -> None:
    content = {
        "type": "object",
        "properties": {
            "format": {
                "type": "string",
                "description": "Output format",
                "enum": ["json", "yaml"],
                "default": "json",
            },
        },
    }
    text = extract_document_text(content)
    assert text is not None
    assert "Output format" in text
    assert "Default: json" in text
    assert "Options: json, yaml" in text


def test_extract_json_catalog_document_from_chunk() -> None:
    chunk = {
        "content": {
            "inputSchema": {
                "properties": {
                    "read_path": {"type": "string", "description": "Read files from disk path"},
                },
            },
        },
    }
    text = extract_json_catalog_document(chunk)
    assert text == "Read files from disk path"


def test_stemming_ranks_reading_above_unrelated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    data = _catalog_from_tools([_make_tool()])
    config = {
        "models": {
            "bm25": {
                "index_dir": str(tmp_path / "bm25"),
                "mmap": True,
                "stem_language": "english",
                "stopwords": "en",
            },
            "rerankers": {"minimum_tools": 29},
            "llm": {"minimum_tools": 50},
        },
        "pruning": {"pipeline": ["bm25"], "per_tool": {}},
    }

    scored, _usage = bm25_catalog_dict(
        copy.deepcopy(data),
        "reading files from disk",
        prune=False,
        config=config,
    )
    json_items = scored["json"]
    read_item = next(
        item
        for item in json_items
        if isinstance(item, dict) and "read_path" in str(item.get("file_path", ""))
    )
    write_item = next(
        item
        for item in json_items
        if isinstance(item, dict) and "write_path" in str(item.get("file_path", ""))
    )
    assert float(read_item["score"]) > float(write_item["score"])

    fingerprint = catalog_fingerprint(data, config=config)
    index_dir = tmp_path / "bm25" / fingerprint
    assert index_dir.exists()

    reloaded = build_or_load_index(data, config=config)
    assert reloaded is not None
    reloaded_scores: list[float] = []
    for item in json_items:
        if "read_path" in str(item.get("file_path", "")):
            reloaded_scores.append(float(item["score"]))
    assert reloaded.retriever is not None


def test_bm25_prune_drops_low_scoring_chunks() -> None:
    data = {
        "json": [
            {"file_path": "a.json", "score": "0.90000000000000000000"},
            {"file_path": "b.json", "score": "0.00010000000000000000"},
        ],
        "md": [],
    }
    pruned = prune_bm25_catalog(copy.deepcopy(data))
    assert len(pruned["json"]) == 1
    assert pruned["json"][0]["file_path"] == "a.json"


def test_fingerprint_changes_with_stem_language() -> None:
    data = _catalog_from_tools([_make_tool()])
    base_config: dict[str, Any] = {
        "models": {
            "bm25": {"stem_language": "english", "stopwords": "en"},
            "rerankers": {"minimum_tools": 29},
            "llm": {"minimum_tools": 50},
        },
        "pruning": {"pipeline": ["bm25"], "per_tool": {}},
    }
    fp_en = catalog_fingerprint(data, config=base_config)
    fp_other = catalog_fingerprint(
        data,
        config={
            **base_config,
            "models": {
                **base_config["models"],
                "bm25": {"stem_language": "spanish", "stopwords": "en"},
            },
        },
    )
    assert fp_en != fp_other


def test_effective_pruning_pipeline_fallbacks() -> None:
    config = {
        "pruning": {"pipeline": [], "per_tool": {}},
        "models": {
            "rerankers": {"minimum_tools": 29},
            "llm": {"minimum_tools": 50},
            "bm25": {"stem_language": "english", "stopwords": "en"},
        },
    }
    assert effective_pruning_pipeline(config, tool_count=5) == ["bm25"]

    config_rerank = {**config, "pruning": {"pipeline": ["rerank"], "per_tool": {}}}
    assert effective_pruning_pipeline(config_rerank, tool_count=5) == ["bm25"]
    assert effective_pruning_pipeline(config_rerank, tool_count=50) == ["rerank"]

    config_both = {**config, "pruning": {"pipeline": ["rerank", "llm"], "per_tool": {}}}
    assert effective_pruning_pipeline(config_both, tool_count=40) == ["rerank"]


def test_mitigate_empty_optional_properties_with_bm25_stage() -> None:
    from cyt.pruners.policies import (
        EMPTY_OPTIONAL_FALLBACK_K,
        direct_root_optional_chunks_for_tool,
        filter_recompose_json_entries,
    )

    tool = {
        "id": "mcp__test__empty_optional",
        "server": "test",
        "tool": "mcp__test__empty_optional",
        "summary": "All optional",
        "full_schema": {
            "id": "mcp__test__empty_optional",
            "name": "mcp__test__empty_optional",
            "description": "All optional props",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "alpha": {"type": "string", "description": "alpha"},
                    "beta": {"type": "string", "description": "beta"},
                    "gamma": {"type": "string", "description": "gamma"},
                },
            },
        },
    }
    index = build_catalog_index([tool], [])
    catalog = index.to_catalog_dict()
    root_path_suffix = "mcp__test__empty_optional.json"
    root = copy.deepcopy(
        next(
            item
            for item in catalog["json"]
            if str(item.get("file_path", "")).endswith(root_path_suffix)
        ),
    )
    optional = [
        copy.deepcopy(item)
        for item in catalog["json"]
        if not str(item.get("file_path", "")).endswith(root_path_suffix)
    ]
    scores = {"alpha": 0.9, "beta": 0.8, "gamma": 0.7}
    for item in optional:
        prop = str(item.get("file_path", "")).split("/")[-1].replace(".json", "")
        item["score"] = f"{scores.get(prop, 0.0):.20f}"

    filtered = filter_recompose_json_entries([root])
    mitigated = mitigate_empty_optional_properties(
        filtered,
        catalog_index=index,
        post_rerank_scored={"json": [root, *optional]},
        pipeline=["bm25"],
    )
    added = direct_root_optional_chunks_for_tool(mitigated, "mcp__test__empty_optional")
    assert len(added) == EMPTY_OPTIONAL_FALLBACK_K


def test_proxy_falls_back_to_bm25_when_rerank_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    tools = [
        {
            "name": "mcp__test__search",
            "description": "Search files",
            "input_schema": _schema_with_optional(),
        },
    ]

    with patch("cyt.proxy.anthropic.rerank_catalog_dict", side_effect=RuntimeError("rerank down")):
        result = filter_tools_for_query(
            tools,
            "reading files",
            pruning_pipeline=["rerank"],
        )

    assert result.status == "applied"
    assert result.tools is not None
    assert len(result.tools) <= len(tools)


def test_build_bm25_tokenizer_uses_stemmer() -> None:
    tokenizer = build_bm25_tokenizer(
        {
            "models": {
                "bm25": {"stem_language": "english", "stopwords": "en"},
            },
        },
    )
    assert tokenizer.stemmer is not None


def test_bm25_stage_usage_records_stats_identity() -> None:
    usage = bm25_stage_usage()
    assert usage.model_name == BM25_STATS_ID
    assert usage.provider_dns_name == BM25_STATS_ID
    assert usage.provider == BM25_STATS_ID


def test_bm25_catalog_dict_returns_stage_usage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    data = _catalog_from_tools([_make_tool()])
    config = {
        "models": {
            "bm25": {
                "index_dir": str(tmp_path / "bm25"),
                "mmap": True,
                "stem_language": "english",
                "stopwords": "en",
            },
            "rerankers": {"minimum_tools": 29},
            "llm": {"minimum_tools": 50},
        },
        "pruning": {"pipeline": ["bm25"], "per_tool": {}},
    }
    _data, usage = bm25_catalog_dict(copy.deepcopy(data), "reading files", config=config)
    assert usage.model_name == BM25_STATS_ID
    assert usage.provider == BM25_STATS_ID
    assert usage.provider_dns_name == BM25_STATS_ID


def test_bm25_score_threshold_constant_matches_rerank() -> None:
    from cyt.pruners.rerank import RERANK_SCORE

    assert BM25_SCORE == RERANK_SCORE
