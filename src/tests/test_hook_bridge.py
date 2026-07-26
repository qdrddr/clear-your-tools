"""Tests for hook-side pruning bridge."""

from __future__ import annotations

from cyt.proxy.anthropic import PruneResult
from cyt.pruning.hook_bridge import _merge_hook_prune_results


def test_merge_hook_prune_results_success_when_any_source_has_tools() -> None:
    merged = _merge_hook_prune_results(
        {
            "mcpc": PruneResult(
                tools=None,
                status="skipped",
                query="q",
                tools_in=0,
                mcp_tools_in=0,
                tools_out=None,
                error="missing catalog",
            ),
            "executor": PruneResult(
                tools=[{"name": "Shell"}],
                status="applied",
                query="q",
                tools_in=1,
                mcp_tools_in=0,
                tools_out=1,
                error=None,
            ),
        },
    )
    assert merged is not None
    assert merged.status == "applied"
    assert merged.error is None
    assert merged.tools == [{"name": "Shell"}]
