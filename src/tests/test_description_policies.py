"""Tests for prune_*_descriptions tool policies."""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any, cast

from cyt.indexer.build import (
    ToolSchemaSource,
    build_catalog_index,
    collect_enums,
    prepare_system_tool_entry,
    prepare_tool_entry,
)
from cyt.indexer.retrieve import retrieve_tools
from cyt.pruners.policies import (
    RERANK_SCORE,
    ToolPolicy,
    filter_recompose_json_entries,
    is_decomposed_optional_property_chunk,
    is_decomposed_tool_root_chunk,
    output_policy_context_from_config,
    partition_catalog,
)


def _make_entry(name: str, schema: dict[str, Any], *, mcp: bool) -> dict[str, Any]:
    tool = cast(
        ToolSchemaSource,
        SimpleNamespace(name=name, description="Tool description", inputSchema=schema),
    )
    if mcp:
        return prepare_tool_entry("srv", tool)
    return prepare_system_tool_entry(tool)


def _schema_with_optional() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "required_field": {
                "type": "string",
                "description": "Required param description",
            },
            "optional_field": {
                "type": "integer",
                "description": "Optional param description",
            },
        },
        "required": ["required_field"],
    }


def _ctx_call_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Tool name to invoke",
            },
            "arguments": {
                "type": "object",
                "description": "Arguments object to pass to the invoked tool",
                "properties": {
                    "create": {
                        "type": "boolean",
                        "description": "Create a new file",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "Replacement text",
                    },
                },
            },
        },
        "required": ["name"],
    }


def _root_and_optionals(
    catalog: dict[str, Any],
    tool_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    roots = [
        x
        for x in catalog["json"]
        if is_decomposed_tool_root_chunk(x) and tool_id in str(x.get("file_path", ""))
    ]
    optionals = [
        x
        for x in catalog["json"]
        if is_decomposed_optional_property_chunk(x) and tool_id in str(x.get("file_path", ""))
    ]
    assert len(roots) == 1
    return roots[0], optionals


def _recompose_tool(
    entry: dict[str, Any],
    *,
    system: ToolPolicy = "prune_optional_descriptions",
    mcp: ToolPolicy = "prune_all_descriptions",
    surviving_chunks: list[dict[str, Any]],
    pipeline: list[str] | None = None,
) -> dict[str, Any]:
    index = build_catalog_index([entry], collect_enums(entry["full_schema"]["inputSchema"]))
    build_catalog = index.to_catalog_dict()
    output_ctx = output_policy_context_from_config(system=system, mcp=mcp)
    tool_id = entry["id"]
    tools = retrieve_tools(
        {"json": surviving_chunks, "md": build_catalog.get("md", [])},
        catalog=index,
        apply_decomposed_score_filter=False,
        ctx=output_ctx,
    )
    return next(t for t in tools if t.get("name") == tool_id)


def test_prune_optional_descriptions_reinstates_optional_without_description() -> None:
    entry = _make_entry("TaskOutput", _schema_with_optional(), mcp=False)
    index = build_catalog_index([entry], collect_enums(entry["full_schema"]["inputSchema"]))
    catalog = index.to_catalog_dict()
    _root_and_optionals(catalog, "TaskOutput")
    _, pinned = partition_catalog(
        catalog,
        output_policy_context_from_config(system="prune_optional_descriptions", mcp="prune_all"),
    )
    assert pinned["json"]

    tool = _recompose_tool(
        entry,
        system="prune_optional_descriptions",
        mcp="prune_all",
        surviving_chunks=[pinned["json"][0]],
    )
    props = tool["inputSchema"]["properties"]
    assert "description" in props["required_field"]
    assert "description" not in props["optional_field"]
    assert props["optional_field"]["type"] == "integer"


def test_prune_all_descriptions_root_survives_reinstates_optional() -> None:
    entry = _make_entry("mcp__test__goal", _schema_with_optional(), mcp=True)
    index = build_catalog_index([entry], collect_enums(entry["full_schema"]["inputSchema"]))
    catalog = index.to_catalog_dict()
    root, _ = _root_and_optionals(catalog, "mcp__test__goal")
    high_root = copy.deepcopy(root)
    high_root["score"] = 1.0

    tool = _recompose_tool(
        entry,
        mcp="prune_all_descriptions",
        surviving_chunks=[high_root],
    )
    assert "description" in tool.get("description", "") or tool.get("description")
    props = tool["inputSchema"]["properties"]
    assert "description" in props["required_field"]
    assert "description" not in props["optional_field"]


def test_prune_all_descriptions_root_pruned_reinstates_required_only() -> None:
    entry = _make_entry("mcp__test__goal", _schema_with_optional(), mcp=True)
    tool = _recompose_tool(
        entry,
        mcp="prune_all_descriptions",
        surviving_chunks=[],
    )
    assert "description" not in tool
    props = tool["inputSchema"]["properties"]
    assert list(props.keys()) == ["required_field"]
    assert "description" not in props["required_field"]


def test_prune_all_descriptions_root_pruned_drops_leaked_optional_chunks() -> None:
    """Optional chunks that pass prune_all filter must not appear in case #1 output."""
    entry = _make_entry("mcp__test__goal", _schema_with_optional(), mcp=True)
    index = build_catalog_index([entry], collect_enums(entry["full_schema"]["inputSchema"]))
    catalog = index.to_catalog_dict()
    _, optionals = _root_and_optionals(catalog, "mcp__test__goal")
    leaked_optional = copy.deepcopy(optionals[0])
    leaked_optional["score"] = 1.0
    from cyt.pruners.policies import scoring_policy_context

    score_ctx = scoring_policy_context(
        output_policy_context_from_config(mcp="prune_all_descriptions"),
    )
    surviving = filter_recompose_json_entries([leaked_optional], ctx=score_ctx)
    assert surviving == [leaked_optional]
    tool = _recompose_tool(
        entry,
        mcp="prune_all_descriptions",
        surviving_chunks=surviving,
    )
    assert "description" not in tool
    props = tool["inputSchema"]["properties"]
    assert list(props.keys()) == ["required_field"]
    assert "description" not in props["required_field"]


def test_nested_partial_survival_per_chunk_descriptions() -> None:
    entry = _make_entry("mcp__lean_ctx__ctx_call", _ctx_call_schema(), mcp=True)
    index = build_catalog_index([entry], collect_enums(entry["full_schema"]["inputSchema"]))
    catalog = index.to_catalog_dict()
    root, optionals = _root_and_optionals(catalog, "mcp__lean_ctx__ctx_call")
    by_suffix = {
        str(item["file_path"]).split("/")[-1].replace(".json", ""): item for item in optionals
    }
    arguments = copy.deepcopy(by_suffix["arguments"])
    arguments["score"] = 1.0
    new_string = copy.deepcopy(by_suffix["new_string"])
    new_string["score"] = 1.0

    surviving = filter_recompose_json_entries(
        [root, arguments, new_string],
        ctx=output_policy_context_from_config(mcp="prune_optional_descriptions"),
    )
    tool = _recompose_tool(
        entry,
        mcp="prune_optional_descriptions",
        surviving_chunks=surviving,
    )
    props = tool["inputSchema"]["properties"]
    assert "description" in props["name"]
    args = props["arguments"]
    assert args["description"] == "Arguments object to pass to the invoked tool"
    assert "description" not in args["properties"]["create"]
    assert args["properties"]["new_string"]["description"] == "Replacement text"


def test_nested_all_optional_pruned_reinstated_without_descriptions() -> None:
    entry = _make_entry("mcp__lean_ctx__ctx_call", _ctx_call_schema(), mcp=True)
    index = build_catalog_index([entry], collect_enums(entry["full_schema"]["inputSchema"]))
    catalog = index.to_catalog_dict()
    _root_and_optionals(catalog, "mcp__lean_ctx__ctx_call")
    _, pinned = partition_catalog(
        catalog,
        output_policy_context_from_config(mcp="prune_optional_descriptions"),
    )

    tool = _recompose_tool(
        entry,
        mcp="prune_optional_descriptions",
        surviving_chunks=[pinned["json"][0]],
    )
    props = tool["inputSchema"]["properties"]
    args = props["arguments"]
    assert "description" not in args
    assert "description" not in args["properties"]["create"]
    assert "description" not in args["properties"]["new_string"]


def test_prune_optional_descriptions_root_pruned_reinstates_required_with_descriptions() -> None:
    """When the entire tool is pruned, prune_optional_descriptions still outputs required props."""
    entry = _make_entry("mcp__test__goal", _schema_with_optional(), mcp=True)
    tool = _recompose_tool(
        entry,
        mcp="prune_optional_descriptions",
        surviving_chunks=[],
    )
    assert tool.get("description")
    props = tool["inputSchema"]["properties"]
    assert "description" in props["required_field"]
    assert "description" not in props["optional_field"]
    assert props["optional_field"]["type"] == "integer"


def test_filter_recompose_uses_scoring_policy_for_descriptions_variant() -> None:
    entry = _make_entry("Bash", _schema_with_optional(), mcp=False)
    catalog = build_catalog_index(
        [entry],
        collect_enums(entry["full_schema"]["inputSchema"]),
    ).to_catalog_dict()
    root, optionals = _root_and_optionals(catalog, "Bash")
    low = copy.deepcopy(optionals[0])
    low["score"] = 0.0001
    ctx = output_policy_context_from_config(system="prune_optional_descriptions", mcp="prune_all")
    from cyt.pruners.policies import scoring_policy_context

    filtered = filter_recompose_json_entries(
        [root, low],
        ctx=scoring_policy_context(ctx),
    )
    assert filtered == [root]
    assert low["score"] < RERANK_SCORE
