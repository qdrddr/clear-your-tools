"""Shared wrapper around proxy tool pruning for hook injection."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from cyt.proxy.anthropic import PruneResult, filter_tools_for_query
from cyt.pruners.remote import PrunerSettingsCache
from cyt.tools.executor_adapter import (
    prefix_tools_for_rust,
    restore_executor_addresses,
    should_adapt_executor_tools_for_rust,
)


def prune_tools_for_query(
    tools: list[dict[str, Any]],
    query: str,
    *,
    config: dict[str, Any],
    pruner_settings: PrunerSettingsCache | None = None,
) -> PruneResult:
    """Run the standard pruning pipeline on a hook-loaded tool catalog."""
    address_by_rust_name: dict[str, str] = {}
    catalog_tools = tools
    if should_adapt_executor_tools_for_rust(config):
        catalog_tools, address_by_rust_name = prefix_tools_for_rust(tools)

    result = filter_tools_for_query(
        catalog_tools,
        query,
        config=config,
        pruner_settings=pruner_settings,
        for_hook=True,
    )
    if not address_by_rust_name:
        return result

    return replace(
        result,
        tools=restore_executor_addresses(result.tools, address_by_rust_name),
        tools_accepted=restore_executor_addresses(result.tools_accepted, address_by_rust_name),
        tools_final=restore_executor_addresses(result.tools_final, address_by_rust_name),
    )
