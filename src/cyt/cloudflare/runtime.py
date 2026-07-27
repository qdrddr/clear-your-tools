"""Injectable runtime hooks for Cloudflare catalog integration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

_load_config: Callable[[], dict[str, Any]] | None = None
_resolve_credential: Callable[..., tuple[str | None, str | None]] | None = None
_connection_health_flapping_settings: Callable[[dict[str, Any] | None], dict[str, Any]] | None = (
    None
)
_tools_hook_cloudflare_cache_settings: Callable[[dict[str, Any] | None], dict[str, Any]] | None = (
    None
)
_tools_hook_cloudflare_url: Callable[[dict[str, Any] | None], str] | None = None
_tools_hook_cloudflare_access_client_id_var: Callable[[dict[str, Any] | None], str] | None = None
_tools_hook_cloudflare_access_client_secret_var: Callable[[dict[str, Any] | None], str] | None = (
    None
)
_uses_cloudflare_tool_catalog: Callable[[dict[str, Any] | None], bool] | None = None


def configure_runtime(
    *,
    load_config: Callable[[], dict[str, Any]],
    resolve_credential: Callable[..., tuple[str | None, str | None]],
    connection_health_flapping_settings: Callable[[dict[str, Any] | None], dict[str, Any]],
    tools_hook_cloudflare_cache_settings: Callable[[dict[str, Any] | None], dict[str, Any]],
    tools_hook_cloudflare_url: Callable[[dict[str, Any] | None], str],
    tools_hook_cloudflare_access_client_id_var: Callable[[dict[str, Any] | None], str],
    tools_hook_cloudflare_access_client_secret_var: Callable[[dict[str, Any] | None], str],
    uses_cloudflare_tool_catalog: Callable[[dict[str, Any] | None], bool],
) -> None:
    global _load_config
    global _resolve_credential
    global _connection_health_flapping_settings
    global _tools_hook_cloudflare_cache_settings
    global _tools_hook_cloudflare_url
    global _tools_hook_cloudflare_access_client_id_var
    global _tools_hook_cloudflare_access_client_secret_var
    global _uses_cloudflare_tool_catalog
    _load_config = load_config
    _resolve_credential = resolve_credential
    _connection_health_flapping_settings = connection_health_flapping_settings
    _tools_hook_cloudflare_cache_settings = tools_hook_cloudflare_cache_settings
    _tools_hook_cloudflare_url = tools_hook_cloudflare_url
    _tools_hook_cloudflare_access_client_id_var = tools_hook_cloudflare_access_client_id_var
    _tools_hook_cloudflare_access_client_secret_var = tools_hook_cloudflare_access_client_secret_var
    _uses_cloudflare_tool_catalog = uses_cloudflare_tool_catalog


def runtime_configured() -> bool:
    return _load_config is not None


def load_config() -> dict[str, Any]:
    if _load_config is None:
        return {}
    return _load_config()


def resolve_credential(
    name: str,
    *,
    allow_prompt: bool = True,
) -> tuple[str | None, str | None]:
    if _resolve_credential is None:
        import os

        value = os.environ.get(name)
        return (value, "env") if value else (None, None)
    return _resolve_credential(name, allow_prompt=allow_prompt)


def connection_health_flapping_settings(config: dict[str, Any] | None = None) -> dict[str, Any]:
    if _connection_health_flapping_settings is None:
        return {}
    return _connection_health_flapping_settings(config)


def tools_hook_cloudflare_cache_settings(config: dict[str, Any] | None = None) -> dict[str, Any]:
    if _tools_hook_cloudflare_cache_settings is None:
        return {
            "catalog_refresh_seconds": 120.0,
            "server_health_refresh_seconds": 120.0,
            "disk_flush_seconds": 900.0,
        }
    return _tools_hook_cloudflare_cache_settings(config)


def tools_hook_cloudflare_url(config: dict[str, Any] | None = None) -> str:
    if _tools_hook_cloudflare_url is None:
        return ""
    return _tools_hook_cloudflare_url(config)


def tools_hook_cloudflare_access_client_id_var(config: dict[str, Any] | None = None) -> str:
    if _tools_hook_cloudflare_access_client_id_var is None:
        return "CF_ACCESS_CLIENT_ID"
    return _tools_hook_cloudflare_access_client_id_var(config)


def tools_hook_cloudflare_access_client_secret_var(config: dict[str, Any] | None = None) -> str:
    if _tools_hook_cloudflare_access_client_secret_var is None:
        return "CF_ACCESS_CLIENT_SECRET"
    return _tools_hook_cloudflare_access_client_secret_var(config)


def uses_cloudflare_tool_catalog(config: dict[str, Any] | None = None) -> bool:
    if _uses_cloudflare_tool_catalog is None:
        return False
    return _uses_cloudflare_tool_catalog(config)
