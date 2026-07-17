"""MCPC CLI client for Clear Your Tools hook injection."""

from __future__ import annotations

from cyt.mcpc.cache_scheduler import (
    clear_mcpc_cache_schedulers,
    schedule_mcpc_catalog_refresh,
    start_mcpc_cache_scheduler,
    stop_mcpc_cache_scheduler,
)
from cyt.mcpc.catalog import (
    clear_mcpc_catalog_cache,
    get_mcpc_catalog,
    load_mcpc_catalog_from_disk,
    load_mcpc_tools,
    mcpc_catalog_health_snapshot,
)
from cyt.mcpc.catalog_disk import (
    normalize_mcpc_executable_slug,
    raw_catalog_content_hash,
    read_disk_catalog,
    write_disk_catalog,
)
from cyt.mcpc.cli import mcpc_available, run_mcpc, run_mcpc_json
from cyt.mcpc.runtime import configure_runtime, runtime_configured
from cyt.mcpc.session_flapping import (
    FlappingPolicy,
    SessionFlapState,
    clear_flapping_cache,
    flapping_policy_from_config,
    gated_sessions,
)
from cyt.mcpc.session_health import (
    SessionHealthSnapshot,
    SessionKey,
    clear_session_health_cache,
    filter_catalog_by_session_health,
    refresh_session_health,
)

__all__ = [
    "FlappingPolicy",
    "SessionFlapState",
    "SessionHealthSnapshot",
    "SessionKey",
    "clear_flapping_cache",
    "clear_mcpc_cache_schedulers",
    "clear_mcpc_catalog_cache",
    "clear_session_health_cache",
    "configure_runtime",
    "filter_catalog_by_session_health",
    "flapping_policy_from_config",
    "gated_sessions",
    "get_mcpc_catalog",
    "load_mcpc_catalog_from_disk",
    "load_mcpc_tools",
    "mcpc_available",
    "mcpc_catalog_health_snapshot",
    "normalize_mcpc_executable_slug",
    "raw_catalog_content_hash",
    "read_disk_catalog",
    "refresh_session_health",
    "run_mcpc",
    "run_mcpc_json",
    "runtime_configured",
    "schedule_mcpc_catalog_refresh",
    "start_mcpc_cache_scheduler",
    "stop_mcpc_cache_scheduler",
    "write_disk_catalog",
]
