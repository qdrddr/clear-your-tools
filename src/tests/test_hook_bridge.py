"""Tests for hook-side pruning bridge."""

from __future__ import annotations

from cyt.cloudflare.mcp import EXCLUDED_TOOL_NAMES
from cyt.proxy.anthropic import PruneResult
from cyt.pruning.hook_bridge import _hook_tool_sources, _merge_hook_prune_results


def test_hook_tool_sources_excludes_cloudflare_portal_admin_tools() -> None:
    catalog = [
        {"name": "context7_query-docs", "cyt_catalog_source": "cloudflare"},
        {"name": "portal_list_servers", "cyt_catalog_source": "cloudflare"},
        {"name": "portal_codemode_search", "cyt_catalog_source": "cloudflare"},
        {"name": "Shell", "cyt_catalog_source": "executor"},
    ]
    sources = _hook_tool_sources(catalog, {})
    cloudflare = next(source for source in sources if source.source_id == "cloudflare")
    names = {tool["name"] for tool in cloudflare.tools}
    assert names == {"context7_query-docs"}
    assert not names & EXCLUDED_TOOL_NAMES


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
