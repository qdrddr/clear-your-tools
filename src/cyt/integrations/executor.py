"""Wire cyt.executor runtime hooks to cyt config and credentials."""

from __future__ import annotations


def wire_executor_runtime() -> None:
    """Connect ``cyt.executor`` to ``cyt.config`` and ``cyt.launch.secrets``."""
    from cyt.config import (
        connection_health_flapping_settings,
        load_config,
        tools_hook_executor_cache_settings,
        tools_hook_executor_token_var,
        tools_hook_executor_url,
        uses_executor_tool_catalog,
    )
    from cyt.executor.runtime import configure_runtime, runtime_configured
    from cyt.launch.secrets import resolve_credential

    if runtime_configured():
        return
    configure_runtime(
        load_config=load_config,
        resolve_credential=resolve_credential,
        connection_health_flapping_settings=connection_health_flapping_settings,
        tools_hook_executor_cache_settings=tools_hook_executor_cache_settings,
        tools_hook_executor_url=tools_hook_executor_url,
        tools_hook_executor_token_var=tools_hook_executor_token_var,
        uses_executor_tool_catalog=uses_executor_tool_catalog,
    )
