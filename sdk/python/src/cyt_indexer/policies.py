"""Tool policy helpers (Rust-backed)."""

from __future__ import annotations

from typing import Any, Literal

import cyt_indexer._native as _native
from cyt_indexer._native import PolicyContext
from cyt_indexer.build import CatalogIndex

__all__ = ["PolicyContext"]

# Typing aliases — canonical strings come from Rust `tool_policies()`.
SystemToolPolicy = Literal[
    "always_include",
    "prune_optional",
    "prune_all",
    "prune_optional_descriptions",
    "prune_all_descriptions",
]
MCPToolPolicy = Literal[
    "always_include",
    "prune_optional",
    "prune_all",
    "prune_optional_descriptions",
    "prune_all_descriptions",
]
ToolPolicy = Literal[
    "always_include",
    "prune_optional",
    "prune_all",
    "prune_optional_descriptions",
    "prune_all_descriptions",
]


def tool_policies() -> tuple[str, ...]:
    """Return valid tool policy strings from Rust."""
    return tuple(_native.tool_policies())


CatalogDict = dict[str, Any]
PinnedCatalog = dict[str, Any]


def policy_context_from_values(config: dict[str, Any]) -> PolicyContext:
    return _native.policy_context_from_values(config)


def effective_policy(tool_id: str, ctx: PolicyContext) -> ToolPolicy:
    return _native.effective_policy(ctx, tool_id)  # type: ignore[return-value]


def tool_pass_through(tool_id: str, ctx: PolicyContext) -> bool:
    return _native.tool_pass_through(ctx, tool_id)


def root_tool_id_from_chunk(item: dict[str, Any]) -> str:
    return _native.root_tool_id_from_chunk(item)


def request_pass_through(tools: list[dict[str, Any]], ctx: PolicyContext) -> bool:
    return _native.request_pass_through(ctx, tools)


def is_non_system_tool_id(tool_id: str) -> bool:
    return _native.is_non_system_tool_id(tool_id)


def is_system_tool_id(tool_id: str) -> bool:
    return _native.is_system_tool_id(tool_id)


def chunk_tool_id(item: dict[str, Any]) -> str:
    return _native.chunk_tool_id(item)


def is_non_system_chunk(item: dict[str, Any]) -> bool:
    return _native.is_non_system_chunk(item)


def is_system_chunk(item: dict[str, Any]) -> bool:
    return _native.is_system_chunk(item)


def is_decomposed_tool_root_chunk(item: dict[str, Any]) -> bool:
    return _native.is_decomposed_tool_root_chunk(item)


def is_decomposed_optional_property_chunk(item: dict[str, Any]) -> bool:
    return _native.is_decomposed_optional_property_chunk(item)


def is_system_root_chunk(item: dict[str, Any]) -> bool:
    return _native.is_system_root_chunk(item)


def is_mcp_root_chunk(item: dict[str, Any]) -> bool:
    return _native.is_mcp_root_chunk(item)


def is_system_optional_chunk(item: dict[str, Any]) -> bool:
    return _native.is_system_optional_chunk(item)


def is_mcp_optional_chunk(item: dict[str, Any]) -> bool:
    return _native.is_mcp_optional_chunk(item)


def classify_optional_chunks_batch(
    items: list[dict[str, Any]],
) -> tuple[list[bool], list[bool]]:
    """Return (system_optional, mcp_optional) flags for catalog items in one native call."""
    result = _native.classify_optional_chunks_batch(items)
    system = result.get("system", [])
    mcp = result.get("mcp", [])
    return [bool(x) for x in system], [bool(x) for x in mcp]


def needs_partition(ctx: PolicyContext) -> bool:
    return _native.needs_partition(ctx)


def needs_pruned_recompose(ctx: PolicyContext) -> bool:
    return _native.needs_pruned_recompose(ctx)


def system_tools_pass_through(ctx: PolicyContext) -> bool:
    return _native.system_tools_pass_through(ctx)


def mcp_tools_pass_through(ctx: PolicyContext) -> bool:
    return _native.mcp_tools_pass_through(ctx)


def full_pass_through(ctx: PolicyContext) -> bool:
    return _native.full_pass_through(ctx)


def catalog_needs_partition(data: CatalogDict, ctx: PolicyContext) -> bool:
    return _native.catalog_needs_partition(data, ctx)


def catalog_needs_pruned_recompose(data: CatalogDict, ctx: PolicyContext) -> bool:
    return _native.catalog_needs_pruned_recompose(data, ctx)


def partition_catalog(data: CatalogDict, ctx: PolicyContext) -> tuple[CatalogDict, PinnedCatalog]:
    proc, pinned = _native.partition_catalog(data, ctx)
    return proc, pinned


def merge_catalog(processed: CatalogDict, pinned: PinnedCatalog) -> CatalogDict:
    return _native.merge_catalog(processed, pinned)


def stash_system_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return list(_native.stash_system_tools(tools))


def restore_system_tools(stash: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return list(_native.restore_system_tools(stash))


def stash_mcp_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return list(_native.stash_mcp_tools(tools))


def restore_mcp_tools(stash: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return list(_native.restore_mcp_tools(stash))


def merge_tools_preserving_order(
    original: list[dict[str, Any]],
    pruned_by_name: dict[str, dict[str, Any]],
    stashed_by_name: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    return list(_native.merge_tools_preserving_order(original, pruned_by_name, stashed_by_name))


def anthropic_tool_is_system(tool: dict[str, Any]) -> bool:
    return _native.anthropic_tool_is_system(tool)


def anthropic_tool_is_mcp(tool: dict[str, Any]) -> bool:
    return _native.anthropic_tool_is_mcp(tool)


def split_anthropic_tools(
    tools: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    non_system, system = _native.split_anthropic_tools(tools)
    return list(non_system), list(system)


def entries_for_policy(
    all_entries: list[dict[str, Any]],
    ctx: PolicyContext,
) -> list[dict[str, Any]]:
    return list(_native.entries_for_policy(ctx, all_entries))


def tools_for_catalog(tools: list[dict[str, Any]], ctx: PolicyContext) -> list[dict[str, Any]]:
    return list(_native.tools_for_catalog(ctx, tools))


def system_required_enum_values(data: CatalogDict) -> frozenset[str]:
    return frozenset(_native.system_required_enum_values(data))


def mcp_required_enum_values(data: CatalogDict) -> frozenset[str]:
    return frozenset(_native.mcp_required_enum_values(data))


def required_enum_values_by_tool(data: CatalogDict) -> dict[str, frozenset[str]]:
    raw = _native.required_enum_values_by_tool(data)
    return {str(k): frozenset(str(x) for x in v) for k, v in raw.items()}


def optional_leaf_survived_rerank(
    item: dict[str, Any],
    *,
    ctx: PolicyContext,
    rerank_score: float | None = None,
    llm_selected_paths: set[str] | None = None,
) -> bool:
    return _native.optional_leaf_survived_rerank(
        item,
        ctx,
        rerank_score,
        llm_selected_paths,
    )


def filter_recompose_json_entries(
    json_list: list[dict[str, Any]],
    *,
    ctx: PolicyContext,
    rerank_score: float | None = None,
    llm_selected_paths: set[str] | None = None,
) -> list[dict[str, Any]]:
    return list(
        _native.filter_recompose_json_entries(
            json_list,
            ctx,
            rerank_score,
            llm_selected_paths,
        ),
    )


def mitigate_empty_optional_properties(
    entries: list[dict[str, Any]],
    *,
    ctx: PolicyContext,
    catalog_index: CatalogIndex,
    post_rerank_scored: dict[str, Any] | None,
    pipeline: list[str],
) -> list[dict[str, Any]]:
    return list(
        _native.mitigate_empty_optional_properties(
            entries,
            catalog_index,
            ctx,
            post_rerank_scored,
            pipeline,
        ),
    )


def append_description_reinstate_entries(
    entries: list[dict[str, Any]],
    *,
    build_catalog: dict[str, Any],
    catalog_index: CatalogIndex,
    ctx: PolicyContext,
) -> list[dict[str, Any]]:
    return list(
        _native.append_description_reinstate_entries(
            entries,
            build_catalog,
            catalog_index,
            ctx,
        ),
    )


def needs_description_reinstate(ctx: PolicyContext) -> bool:
    return _native.needs_description_reinstate(ctx)


def is_description_policy(policy: str) -> bool:
    return _native.is_description_policy(policy)


def scoring_policy(policy: str) -> ToolPolicy:
    return _native.scoring_policy(policy)  # type: ignore[return-value]


def direct_root_optional_chunks_for_tool(
    items: list[dict[str, Any]],
    tool_id: str,
) -> list[dict[str, Any]]:
    return list(_native.direct_root_optional_chunks_for_tool(items, tool_id))


def root_chunk_properties_empty(item: dict[str, Any]) -> bool:
    return _native.root_chunk_properties_empty(item)


def tool_id_has_empty_decomposed_root(catalog_index: CatalogIndex, tool_id: str) -> bool:
    return _native.tool_id_has_empty_decomposed_root(catalog_index, tool_id)


def tool_id_had_empty_original_root_properties(
    catalog_index: CatalogIndex,
    tool_id: str,
) -> bool:
    return _native.tool_id_had_empty_original_root_properties(catalog_index, tool_id)


def is_direct_root_optional_property_chunk(item: dict[str, Any]) -> bool:
    return _native.is_direct_root_optional_property_chunk(item)


def drop_recomposed_tools_with_empty_properties(
    tools: list[dict[str, Any]],
    catalog_index: CatalogIndex,
    ctx: PolicyContext,
) -> list[dict[str, Any]]:
    return list(
        _native.drop_recomposed_tools_with_empty_properties(tools, catalog_index, ctx),
    )
