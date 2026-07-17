"""Executor MCP aggregator client for Clear Your Tools."""

from __future__ import annotations

from cyt.executor.cache_scheduler import (
    clear_executor_cache_schedulers,
    schedule_executor_catalog_refresh,
    start_executor_cache_scheduler,
    stop_executor_cache_scheduler,
)
from cyt.executor.catalog_disk import (
    normalize_executor_url_slug,
    raw_catalog_content_hash,
    raw_connections_health_hash,
    raw_executor_mcp_content_hash,
    read_disk_catalog,
    write_disk_catalog,
)
from cyt.executor.connection_flapping import (
    ConnectionFlapState,
    FlappingPolicy,
    clear_flapping_cache,
    gated_connections,
    is_flapping,
    update_flapping_states,
)
from cyt.executor.connection_health import (
    ConnectionHealthSnapshot,
    ConnectionKey,
    apply_health_snapshot,
    clear_connection_health_cache,
    connection_health_snapshot_fields,
    debug_disk_enabled,
    filter_catalog_by_health,
    filter_summaries_for_schema_fetch,
    refresh_connection_health_async,
    set_executor_debug_disk,
    tool_schema_eligible,
)
from cyt.executor.http import (
    clear_executor_catalog_cache,
    executor_catalog_health_snapshot,
    fetch_executor_tools,
    fetch_executor_tools_for_cli,
    get_executor_catalog,
    get_executor_mcp_cache,
    load_executor_catalog_from_disk,
    load_executor_tools,
)
from cyt.executor.mcp import format_executor_mcp_selector_appendix
from cyt.executor.runtime import configure_runtime, runtime_configured

__all__ = [
    "ConnectionFlapState",
    "ConnectionHealthSnapshot",
    "ConnectionKey",
    "FlappingPolicy",
    "apply_health_snapshot",
    "clear_connection_health_cache",
    "clear_executor_cache_schedulers",
    "clear_executor_catalog_cache",
    "clear_flapping_cache",
    "configure_runtime",
    "connection_health_snapshot_fields",
    "debug_disk_enabled",
    "executor_catalog_health_snapshot",
    "fetch_executor_tools",
    "fetch_executor_tools_for_cli",
    "filter_catalog_by_health",
    "filter_summaries_for_schema_fetch",
    "format_executor_mcp_selector_appendix",
    "gated_connections",
    "get_executor_catalog",
    "get_executor_mcp_cache",
    "is_flapping",
    "load_executor_catalog_from_disk",
    "load_executor_tools",
    "normalize_executor_url_slug",
    "raw_catalog_content_hash",
    "raw_connections_health_hash",
    "raw_executor_mcp_content_hash",
    "read_disk_catalog",
    "refresh_connection_health_async",
    "runtime_configured",
    "schedule_executor_catalog_refresh",
    "set_executor_debug_disk",
    "start_executor_cache_scheduler",
    "stop_executor_cache_scheduler",
    "tool_schema_eligible",
    "update_flapping_states",
    "write_disk_catalog",
]
