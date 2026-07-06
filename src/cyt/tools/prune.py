"""Shared wrapper around proxy tool pruning for hook injection."""

from __future__ import annotations

from typing import Any

from cyt.proxy.anthropic import PruneResult, filter_tools_for_query
from cyt.pruners.remote import PrunerSettingsCache


def prune_tools_for_query(
    tools: list[dict[str, Any]],
    query: str,
    *,
    config: dict[str, Any],
    pruner_settings: PrunerSettingsCache | None = None,
) -> PruneResult:
    """Run the standard pruning pipeline on a hook-loaded tool catalog."""
    return filter_tools_for_query(
        tools,
        query,
        config=config,
        pruner_settings=pruner_settings,
        for_hook=True,
    )
