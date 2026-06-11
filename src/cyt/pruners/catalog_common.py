"""Shared catalog scoring and pruning helpers for BM25, rerank, and LLM stages."""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Callable
from typing import Any

from cyt.indexer.build import catalog_tool_count
from cyt.pruners.policies import (
    MCPToolPolicy,
    PolicyContext,
    SystemToolPolicy,
    catalog_needs_partition,
    full_pass_through,
    merge_catalog,
    partition_catalog,
    policy_context_from_config,
)

logger = logging.getLogger(__name__)


def resolve_policy_context(
    *,
    ctx: PolicyContext | None,
    system_policy: SystemToolPolicy | None,
    mcp_policy: MCPToolPolicy | None,
    config: dict[str, Any] | None,
) -> PolicyContext | None:
    """Build policy context from explicit ctx or legacy system/mcp policy args."""
    if ctx is not None:
        return ctx
    if system_policy is not None or mcp_policy is not None:
        return policy_context_from_config(system=system_policy, mcp=mcp_policy, config=config)
    return None


def prepare_catalog_for_scoring(
    data: dict[str, Any],
    policy_ctx: PolicyContext | None,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    """Partition pinned tools when needed; return (data, pinned, skip_scoring)."""
    if policy_ctx is not None and full_pass_through(policy_ctx):
        return data, {}, True

    pinned: dict[str, Any] = {}
    if policy_ctx is not None and catalog_needs_partition(data, policy_ctx):
        data, pinned = partition_catalog(data, policy_ctx)
    return data, pinned, False


def finalize_catalog_result(
    data: dict[str, Any],
    pinned: dict[str, Any],
    *,
    merge_pinned: bool,
) -> dict[str, Any]:
    """Merge pinned catalog entries back into scored data when requested."""
    if merge_pinned and pinned:
        return merge_catalog(data, pinned)
    return data


def catalog_below_minimum_tools(
    data: dict[str, Any],
    minimum_tools: int,
    *,
    stage: str,
) -> bool:
    """Return True when tool count is below the stage minimum (pruning should skip)."""
    tool_count = catalog_tool_count(data)
    if tool_count < minimum_tools:
        logger.info(
            "%s pruning skipped: %d tools below minimum %d",
            stage,
            tool_count,
            minimum_tools,
        )
        return True
    return False


def prepare_indexed_documents(
    items: list[dict[str, Any]],
    extract_fn: Callable[[dict[str, Any]], str | None],
) -> list[tuple[int, str]]:
    """Reset scores and return (item_index, document_text) pairs for scoring."""
    indexed: list[tuple[int, str]] = []
    for item_index, item in enumerate(items):
        item["score"] = f"{0.0:.20f}"
        if text := extract_fn(item):
            indexed.append((item_index, text))
    return indexed


def prune_catalog_lists(
    data: dict[str, Any],
    *,
    json_threshold: float,
    md_threshold: float,
    prune_enums: bool,
) -> dict[str, Any]:
    """Drop json/md catalog items below score thresholds."""
    json_items = data.get("json")
    if isinstance(json_items, list):
        data["json"] = [
            item for item in json_items if float(item.get("score", 0)) >= json_threshold
        ]

    if prune_enums:
        md_items = data.get("md")
        if isinstance(md_items, list):
            data["md"] = [item for item in md_items if float(item.get("score", 0)) >= md_threshold]

    return data


def load_pruner_catalog_input(
    *,
    json_path: str | None,
    dir_path: str | None,
) -> dict[str, Any]:
    """Load a catalog dict from --json or --dir CLI arguments."""
    if json_path:
        try:
            with open(json_path) as f:
                data_loaded: Any = json.load(f)
        except Exception as exc:
            print(f"Error reading JSON: {exc}", file=sys.stderr)
            sys.exit(1)
        if not isinstance(data_loaded, dict):
            print(f"Error: JSON root must be a dictionary in {json_path}", file=sys.stderr)
            sys.exit(1)
        return data_loaded

    if dir_path:
        from cyt.indexer.retrieve import load_catalog

        try:
            return load_catalog(dir_path)
        except Exception as exc:
            print(f"Error loading catalog directory: {exc}", file=sys.stderr)
            sys.exit(1)

    print("Error: --json or --dir is required.", file=sys.stderr)
    sys.exit(1)
