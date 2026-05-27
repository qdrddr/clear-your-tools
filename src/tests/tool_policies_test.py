"""Tests for system/MCP tool policy chunk classification."""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any

from build_index import build_catalog_index, collect_enums, prepare_system_tool_entry, prepare_tool_entry
from tool_policies import (
    RERANK_SCORE,
    filter_recompose_json_entries,
    is_decomposed_optional_property_chunk,
    is_decomposed_tool_root_chunk,
    is_mcp_optional_chunk,
    is_mcp_root_chunk,
    is_system_optional_chunk,
    is_system_root_chunk,
    partition_catalog,
)


def _make_entry(name: str, schema: dict[str, Any], *, mcp: bool) -> dict[str, Any]:
    tool = SimpleNamespace(name=name, description="Tool description", inputSchema=schema)
    if mcp:
        return prepare_tool_entry("srv", tool)
    return prepare_system_tool_entry(tool)


def _schema_with_optional() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "required_field": {"type": "string"},
            "optional_field": {"type": "integer"},
        },
        "required": ["required_field"],
    }


def test_root_vs_optional_uses_file_path_not_description() -> None:
    """Chunk role comes from decomposition file_path, not content.description."""
    mcp_entry = _make_entry("mcp__test_tool", _schema_with_optional(), mcp=True)
    sys_entry = _make_entry("TaskOutput", _schema_with_optional(), mcp=False)
    enums = collect_enums(mcp_entry["full_schema"]["inputSchema"])
    enums.extend(collect_enums(sys_entry["full_schema"]["inputSchema"]))
    catalog = build_catalog_index([mcp_entry, sys_entry], enums).to_catalog_dict()

    roots = [x for x in catalog["json"] if is_decomposed_tool_root_chunk(x)]
    optionals = [x for x in catalog["json"] if is_decomposed_optional_property_chunk(x)]
    assert len(roots) == 2
    assert len(optionals) == 2

    for opt in optionals:
        # Legacy heuristic would treat chunks with description as roots; force description
        # onto optional chunk content and confirm classification is unchanged.
        with_description = copy.deepcopy(opt)
        content = with_description.get("content")
        assert isinstance(content, dict)
        content["description"] = "injected — must not affect role"
        assert is_decomposed_optional_property_chunk(with_description)
        assert not is_decomposed_tool_root_chunk(with_description)

    for root in roots:
        content = root.get("content")
        assert isinstance(content, dict)
        content.pop("description", None)
        assert is_decomposed_tool_root_chunk(root)
        assert not is_decomposed_optional_property_chunk(root)


def test_system_and_mcp_kind_helpers_use_same_decomposition_role() -> None:
    mcp_entry = _make_entry("mcp__search", _schema_with_optional(), mcp=True)
    sys_entry = _make_entry("TaskOutput", _schema_with_optional(), mcp=False)
    catalog = build_catalog_index(
        [mcp_entry, sys_entry],
        collect_enums(mcp_entry["full_schema"]["inputSchema"]),
    ).to_catalog_dict()

    mcp_roots = [x for x in catalog["json"] if is_mcp_root_chunk(x)]
    mcp_opts = [x for x in catalog["json"] if is_mcp_optional_chunk(x)]
    sys_roots = [x for x in catalog["json"] if is_system_root_chunk(x)]
    sys_opts = [x for x in catalog["json"] if is_system_optional_chunk(x)]

    assert len(mcp_roots) == 1
    assert len(mcp_opts) == 1
    assert len(sys_roots) == 1
    assert len(sys_opts) == 1


def test_partition_pins_system_roots_via_file_path() -> None:
    sys_entry = _make_entry("TaskOutput", _schema_with_optional(), mcp=False)
    catalog = build_catalog_index(
        [sys_entry],
        collect_enums(sys_entry["full_schema"]["inputSchema"]),
    ).to_catalog_dict()

    _, pinned = partition_catalog(catalog, "prune_optional", "prune_all")
    assert len(pinned["json"]) == 1
    assert is_system_root_chunk(pinned["json"][0])
    assert "optional_field" not in str(pinned["json"][0]["content"])


def test_filter_recompose_json_entries_drops_pruned_optionals() -> None:
    sys_entry = _make_entry("Bash", _schema_with_optional(), mcp=False)
    catalog = build_catalog_index(
        [sys_entry],
        collect_enums(sys_entry["full_schema"]["inputSchema"]),
    ).to_catalog_dict()
    roots = [x for x in catalog["json"] if is_decomposed_tool_root_chunk(x)]
    optionals = [x for x in catalog["json"] if is_decomposed_optional_property_chunk(x)]
    assert roots and optionals

    low_score_opt = copy.deepcopy(optionals[0])
    low_score_opt["score"] = 0.0001
    filtered = filter_recompose_json_entries(
        [roots[0], low_score_opt],
        system_policy="prune_optional",
        mcp_policy="prune_all",
    )
    assert filtered == [roots[0]]

    below_threshold_opt = copy.deepcopy(optionals[0])
    below_threshold_opt["score"] = 0.0005
    filtered_weak = filter_recompose_json_entries(
        [roots[0], below_threshold_opt],
        system_policy="prune_optional",
        mcp_policy="prune_all",
    )
    assert filtered_weak == [roots[0]]
    assert below_threshold_opt["score"] < RERANK_SCORE


def test_bash_optional_not_restored_from_full_catalog_index() -> None:
    """Survivor-only recompose must not merge optional chunks present only in the full index."""
    from retrieve_catalog import retrieve_tools
    from tool_policies import partition_catalog

    bash_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "description": {"type": "string", "description": "param desc"},
        },
        "required": ["command"],
    }
    sys_entry = _make_entry("Bash", bash_schema, mcp=False)
    index = build_catalog_index([sys_entry], [])
    catalog = index.to_catalog_dict()
    _, pinned = partition_catalog(catalog, "prune_optional", "prune_all")

    recompose_data = {"json": list(pinned["json"]), "md": []}
    tools = retrieve_tools(
        recompose_data,
        catalog=index,
        apply_decomposed_score_filter=False,
        system_policy="prune_optional",
    )
    bash = next(t for t in tools if t.get("name") == "Bash")
    assert list(bash["inputSchema"]["properties"].keys()) == ["command"]
