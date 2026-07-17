"""Wire cyt.mcpc runtime hooks to cyt config."""

from __future__ import annotations


def wire_mcpc_runtime() -> None:
    """Connect ``cyt.mcpc`` to ``cyt.config``."""
    from cyt.config import (
        connection_health_flapping_settings,
        load_config,
        tools_hook_mcpc_cache_settings,
        tools_hook_mcpc_executable,
        uses_mcpc_tool_catalog,
    )
    from cyt.mcpc.runtime import configure_runtime, runtime_configured

    if runtime_configured():
        return
    configure_runtime(
        load_config=load_config,
        connection_health_flapping_settings=connection_health_flapping_settings,
        tools_hook_mcpc_cache_settings=tools_hook_mcpc_cache_settings,
        tools_hook_mcpc_executable=tools_hook_mcpc_executable,
        uses_mcpc_tool_catalog=uses_mcpc_tool_catalog,
    )
