"""Wire cyt.cloudflare runtime hooks to cyt config."""

from __future__ import annotations


def wire_cloudflare_runtime() -> None:
    from cyt.cloudflare.runtime import configure_runtime, runtime_configured
    from cyt.config import (
        connection_health_flapping_settings,
        load_config,
        tools_hook_cloudflare_access_client_id_var,
        tools_hook_cloudflare_access_client_secret_var,
        tools_hook_cloudflare_cache_settings,
        tools_hook_cloudflare_url,
        uses_cloudflare_tool_catalog,
    )
    from cyt.launch.secrets import resolve_credential

    if runtime_configured():
        return
    configure_runtime(
        load_config=load_config,
        resolve_credential=resolve_credential,
        connection_health_flapping_settings=connection_health_flapping_settings,
        tools_hook_cloudflare_cache_settings=tools_hook_cloudflare_cache_settings,
        tools_hook_cloudflare_url=tools_hook_cloudflare_url,
        tools_hook_cloudflare_access_client_id_var=tools_hook_cloudflare_access_client_id_var,
        tools_hook_cloudflare_access_client_secret_var=tools_hook_cloudflare_access_client_secret_var,
        uses_cloudflare_tool_catalog=uses_cloudflare_tool_catalog,
    )
