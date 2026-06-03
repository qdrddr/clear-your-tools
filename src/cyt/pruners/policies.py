"""System vs MCP tool policies — app config + Rust-backed core."""

from __future__ import annotations

from typing import Any

from cyt_indexer.policies import (
    CatalogDict,
    MCPToolPolicy,
    PinnedCatalog,
    PolicyContext,
    SystemToolPolicy,
    ToolPolicy,
    anthropic_tool_is_mcp,
    anthropic_tool_is_system,
    catalog_needs_partition,
    catalog_needs_pruned_recompose,
    chunk_tool_id,
    direct_root_optional_chunks_for_tool,
    drop_recomposed_tools_with_empty_properties,
    effective_policy,
    entries_for_policy,
    filter_recompose_json_entries,
    is_decomposed_optional_property_chunk,
    is_decomposed_tool_root_chunk,
    is_direct_root_optional_property_chunk,
    is_mcp_optional_chunk,
    is_mcp_root_chunk,
    is_non_system_chunk,
    is_non_system_tool_id,
    is_system_chunk,
    is_system_optional_chunk,
    is_system_root_chunk,
    is_system_tool_id,
    mcp_required_enum_values,
    mcp_tools_pass_through,
    merge_catalog,
    merge_tools_preserving_order,
    mitigate_empty_optional_properties,
    needs_partition,
    needs_pruned_recompose,
    optional_leaf_survived_rerank,
    partition_catalog,
    policy_context_from_values,
    required_enum_values_by_tool,
    restore_mcp_tools,
    restore_system_tools,
    root_chunk_properties_empty,
    root_tool_id_from_chunk,
    split_anthropic_tools,
    stash_mcp_tools,
    stash_system_tools,
    system_required_enum_values,
    system_tools_pass_through,
    tool_id_had_empty_original_root_properties,
    tool_id_has_empty_decomposed_root,
    tool_pass_through,
    tools_for_catalog,
)
from cyt_indexer.policies import (
    full_pass_through as _full_pass_through_ctx,
)
from cyt_indexer.policies import (
    request_pass_through as _request_pass_through_ctx,
)

from cyt.common.runtime_constants import EMPTY_OPTIONAL_FALLBACK_K, RERANK_SCORE
from cyt.config import load_config

__all__ = [
    "EMPTY_OPTIONAL_FALLBACK_K",
    "RERANK_SCORE",
    "CatalogDict",
    "MCPToolPolicy",
    "PinnedCatalog",
    "PolicyContext",
    "SystemToolPolicy",
    "ToolPolicy",
    "anthropic_tool_is_mcp",
    "anthropic_tool_is_system",
    "catalog_needs_partition",
    "catalog_needs_pruned_recompose",
    "chunk_tool_id",
    "configure_policies_from_config",
    "direct_root_optional_chunks_for_tool",
    "drop_recomposed_tools_with_empty_properties",
    "effective_policy",
    "entries_for_policy",
    "filter_recompose_json_entries",
    "full_pass_through",
    "is_decomposed_optional_property_chunk",
    "is_decomposed_tool_root_chunk",
    "is_direct_root_optional_property_chunk",
    "is_mcp_optional_chunk",
    "is_mcp_root_chunk",
    "is_non_system_chunk",
    "is_non_system_tool_id",
    "is_system_chunk",
    "is_system_optional_chunk",
    "is_system_root_chunk",
    "is_system_tool_id",
    "mcp_required_enum_values",
    "mcp_tools_pass_through",
    "merge_catalog",
    "merge_tools_preserving_order",
    "mitigate_empty_optional_properties",
    "needs_partition",
    "needs_pruned_recompose",
    "optional_leaf_survived_rerank",
    "partition_catalog",
    "policy_context_from_config",
    "request_pass_through",
    "required_enum_values_by_tool",
    "restore_mcp_tools",
    "restore_system_tools",
    "root_chunk_properties_empty",
    "root_tool_id_from_chunk",
    "split_anthropic_tools",
    "stash_mcp_tools",
    "stash_system_tools",
    "system_required_enum_values",
    "system_tools_pass_through",
    "tool_id_had_empty_original_root_properties",
    "tool_id_has_empty_decomposed_root",
    "tool_pass_through",
    "tools_for_catalog",
]


def full_pass_through(
    ctx_or_system: PolicyContext | SystemToolPolicy,
    mcp: MCPToolPolicy | None = None,
) -> bool:
    """Return True when no catalog pruning is required for the given policies."""
    if isinstance(ctx_or_system, PolicyContext):
        return _full_pass_through_ctx(ctx_or_system)
    if mcp is None:
        raise TypeError("mcp policy required when passing system_policy as a string")
    return _full_pass_through_ctx(policy_context_from_config(system=ctx_or_system, mcp=mcp))


def request_pass_through(
    tools: list[dict[str, Any]],
    ctx: PolicyContext | None = None,
) -> bool:
    """Return True when the whole tool list should skip pruning."""
    policy_ctx = ctx or policy_context_from_config()
    return _request_pass_through_ctx(tools, policy_ctx)


def policy_context_from_config(
    config: dict[str, Any] | None = None,
    *,
    system: SystemToolPolicy | None = None,
    mcp: MCPToolPolicy | None = None,
    per_tool: dict[str, ToolPolicy] | None = None,
) -> PolicyContext:
    """Build per-request policy context from app config and optional overrides."""
    if config is None:
        config = load_config()
    ctx = policy_context_from_values(config)
    if system is not None:
        ctx.system_policy = system
    if mcp is not None:
        ctx.mcp_policy = mcp
    if per_tool:
        merged = dict(ctx.per_tool)
        merged.update(per_tool)
        ctx.per_tool = merged
    return ctx


def configure_policies_from_config(config: dict[str, Any] | None = None) -> PolicyContext:
    """Return policy context from config (replaces mutating module globals)."""
    return policy_context_from_config(config)
