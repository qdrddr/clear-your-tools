"""Integration tests for per-enum-array top-3 + threshold filtering."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from cyt.indexer.build import (
    ToolSchemaSource,
    build_catalog_index,
    collect_enums,
    prepare_tool_entry,
)
from cyt.indexer.pipeline import recompose_and_retrieve_tools
from cyt.pruners.policies import output_policy_context_from_config, policy_context_from_config


def _make_mcp_tool(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    tool = cast(
        ToolSchemaSource,
        SimpleNamespace(name=name, description=f"{name} tool", inputSchema=schema),
    )
    return prepare_tool_entry("test", tool)


def _root_chunks(catalog: dict[str, Any], tool_name: str) -> list[dict[str, Any]]:
    json_items = catalog.get("json")
    if not isinstance(json_items, list):
        return []
    roots: list[dict[str, Any]] = []
    for item in json_items:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, dict):
            continue
        if content.get("name") == tool_name:
            roots.append({**item, "score": 1.0})
    return roots


def _md_entries(scores: dict[str, float]) -> list[dict[str, Any]]:
    return [{"content": value, "score": score} for value, score in scores.items()]


@pytest.fixture(scope="module", autouse=True)
def _bootstrap_sdk() -> None:
    from cyt_core.bootstrap import bootstrap

    bootstrap()


def test_recompose_and_retrieve_keeps_top_three_enums_per_property() -> None:
    hedl_schema = {
        "type": "object",
        "properties": {
            "hedl": {"type": "string"},
            "format": {
                "type": "string",
                "enum": ["json", "yaml", "xml", "csv", "parquet", "cypher", "toon"],
            },
        },
        "required": ["hedl", "format"],
    }
    index_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "identity_mode": {
                "type": "string",
                "enum": ["config", "local", "git"],
            },
        },
        "required": ["path"],
    }
    hedl_entry = _make_mcp_tool("hedl_convert_to", hedl_schema)
    index_entry = _make_mcp_tool("index_folder", index_schema)
    enums = collect_enums(hedl_schema)
    enums.extend(collect_enums(index_schema))
    index = build_catalog_index([hedl_entry, index_entry], enums)
    catalog = index.to_catalog_dict()

    format_scores = {
        "yaml": 0.95,
        "csv": 0.89,
        "json": 0.0005,
        "xml": 0.0004,
        "parquet": 0.0003,
        "cypher": 0.0002,
        "toon": 0.0001,
    }
    identity_scores = {
        "git": 0.9,
        "local": 0.0008,
        "config": 0.00006,
    }
    all_scores = {**format_scores, **identity_scores}
    post_rerank_scored = {
        "json": _root_chunks(catalog, "hedl_convert_to") + _root_chunks(catalog, "index_folder"),
        "md": _md_entries(all_scores),
    }
    pruned_md = _md_entries({k: v for k, v in all_scores.items() if v >= 0.0001})
    data = {
        "json": post_rerank_scored["json"],
        "md": pruned_md,
    }
    ctx = policy_context_from_config(system="prune_optional", mcp="prune_all")
    output_ctx = output_policy_context_from_config(system="prune_optional", mcp="prune_all")

    tools = recompose_and_retrieve_tools(
        data,
        catalog,
        index,
        post_rerank_scored,
        post_rerank_scored,
        None,
        ["rerank"],
        ctx,
        output_ctx,
    )
    by_name = {tool.get("name"): tool for tool in tools if isinstance(tool, dict)}

    hedl = by_name["hedl_convert_to"]
    index_tool = by_name["index_folder"]
    format_enum = hedl["inputSchema"]["properties"]["format"]["enum"]
    identity_enum = index_tool["inputSchema"]["properties"]["identity_mode"]["enum"]

    assert format_enum == ["yaml", "csv", "json"]
    assert identity_enum == ["git", "local", "config"]


def test_recompose_and_retrieve_keeps_top_three_enums_with_llm_scores() -> None:
    hedl_schema = {
        "type": "object",
        "properties": {
            "hedl": {"type": "string"},
            "format": {
                "type": "string",
                "enum": ["json", "yaml", "xml", "csv", "parquet", "cypher", "toon"],
            },
        },
        "required": ["hedl", "format"],
    }
    index_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "identity_mode": {
                "type": "string",
                "enum": ["config", "local", "git"],
            },
        },
        "required": ["path"],
    }
    hedl_entry = _make_mcp_tool("hedl_convert_to", hedl_schema)
    index_entry = _make_mcp_tool("index_folder", index_schema)
    enums = collect_enums(hedl_schema)
    enums.extend(collect_enums(index_schema))
    index = build_catalog_index([hedl_entry, index_entry], enums)
    catalog = index.to_catalog_dict()

    format_scores = {
        "yaml": 0.95,
        "csv": 0.89,
        "json": 0.005,
        "xml": 0.004,
        "parquet": 0.003,
        "cypher": 0.002,
        "toon": 0.001,
    }
    identity_scores = {
        "git": 0.90,
        "local": 0.008,
        "config": 0.0006,
    }
    all_scores = {**format_scores, **identity_scores}
    post_llm_scored = {
        "json": _root_chunks(catalog, "hedl_convert_to") + _root_chunks(catalog, "index_folder"),
        "md": _md_entries(all_scores),
    }
    pruned_md = _md_entries({k: v for k, v in all_scores.items() if v >= 0.001})
    data = {
        "json": post_llm_scored["json"],
        "md": pruned_md,
    }
    ctx = policy_context_from_config(system="prune_optional", mcp="prune_all")
    output_ctx = output_policy_context_from_config(system="prune_optional", mcp="prune_all")

    tools = recompose_and_retrieve_tools(
        data,
        catalog,
        index,
        post_llm_scored,
        post_llm_scored,
        None,
        ["llm"],
        ctx,
        output_ctx,
    )
    by_name = {tool.get("name"): tool for tool in tools if isinstance(tool, dict)}

    hedl = by_name["hedl_convert_to"]
    index_tool = by_name["index_folder"]
    format_enum = hedl["inputSchema"]["properties"]["format"]["enum"]
    identity_enum = index_tool["inputSchema"]["properties"]["identity_mode"]["enum"]

    assert format_enum == ["yaml", "csv", "json"]
    assert identity_enum == ["git", "local", "config"]


def test_recompose_injects_root_when_optional_json_survives() -> None:
    schema = {
        "type": "object",
        "properties": {
            "required_field": {"type": "string"},
            "optional_field": {"type": "string", "description": "opt"},
        },
        "required": ["required_field"],
    }
    entry = _make_mcp_tool("mcp__test__root_inject", schema)
    index = build_catalog_index([entry], collect_enums(schema))
    catalog = index.to_catalog_dict()

    optional_items = [
        item
        for item in catalog.get("json", [])
        if isinstance(item, dict) and "optional_field" in str(item.get("file_path", ""))
    ]
    assert optional_items, "expected optional decomposed chunk in catalog"
    optional = {**optional_items[0], "score": 0.9}

    post_scored = {"json": catalog.get("json", []), "md": []}
    data = {"json": [optional], "md": []}
    ctx = policy_context_from_config(system="prune_optional", mcp="prune_all")
    output_ctx = output_policy_context_from_config(system="prune_optional", mcp="prune_all")

    tools = recompose_and_retrieve_tools(
        data,
        catalog,
        index,
        None,
        post_scored,
        None,
        ["bm25"],
        ctx,
        output_ctx,
    )
    assert len(tools) == 1
    tool = tools[0]
    assert tool.get("name") == "mcp__test__root_inject"
    props = (tool.get("inputSchema") or {}).get("properties") or {}
    assert "required_field" in props
    assert "optional_field" in props
