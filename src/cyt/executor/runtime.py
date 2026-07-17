"""Injectable runtime hooks for host application integration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

_load_config: Callable[[], dict[str, Any]] | None = None
_resolve_credential: Callable[..., tuple[str | None, str | None]] | None = None
_connection_health_flapping_settings: Callable[[dict[str, Any] | None], dict[str, Any]] | None = (
    None
)
_tools_hook_executor_cache_settings: Callable[[dict[str, Any] | None], dict[str, Any]] | None = None
_tools_hook_executor_url: Callable[[dict[str, Any] | None], str] | None = None
_tools_hook_executor_token_var: Callable[[dict[str, Any] | None], str] | None = None
_uses_executor_tool_catalog: Callable[[dict[str, Any] | None], bool] | None = None


def configure_runtime(
    *,
    load_config: Callable[[], dict[str, Any]],
    resolve_credential: Callable[..., tuple[str | None, str | None]],
    connection_health_flapping_settings: Callable[[dict[str, Any] | None], dict[str, Any]],
    tools_hook_executor_cache_settings: Callable[[dict[str, Any] | None], dict[str, Any]],
    tools_hook_executor_url: Callable[[dict[str, Any] | None], str],
    tools_hook_executor_token_var: Callable[[dict[str, Any] | None], str],
    uses_executor_tool_catalog: Callable[[dict[str, Any] | None], bool],
) -> None:
    """Wire host-provided config and credential helpers (idempotent)."""
    global _load_config
    global _resolve_credential
    global _connection_health_flapping_settings
    global _tools_hook_executor_cache_settings
    global _tools_hook_executor_url
    global _tools_hook_executor_token_var
    global _uses_executor_tool_catalog
    _load_config = load_config
    _resolve_credential = resolve_credential
    _connection_health_flapping_settings = connection_health_flapping_settings
    _tools_hook_executor_cache_settings = tools_hook_executor_cache_settings
    _tools_hook_executor_url = tools_hook_executor_url
    _tools_hook_executor_token_var = tools_hook_executor_token_var
    _uses_executor_tool_catalog = uses_executor_tool_catalog


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


def tools_hook_executor_cache_settings(config: dict[str, Any] | None = None) -> dict[str, Any]:
    if _tools_hook_executor_cache_settings is None:
        return {
            "health_refresh_seconds": 1,
            "health_probe_concurrency": 4,
            "catalog_schema_refresh_seconds": 120,
            "disk_flush_seconds": 900,
        }
    return _tools_hook_executor_cache_settings(config)


def tools_hook_executor_url(config: dict[str, Any] | None = None) -> str:
    if _tools_hook_executor_url is None:
        return ""
    return _tools_hook_executor_url(config)


def tools_hook_executor_token_var(config: dict[str, Any] | None = None) -> str:
    if _tools_hook_executor_token_var is None:
        return "EXECUTOR_TOKEN"
    return _tools_hook_executor_token_var(config)


def uses_executor_tool_catalog(config: dict[str, Any] | None = None) -> bool:
    if _uses_executor_tool_catalog is None:
        return False
    return _uses_executor_tool_catalog(config)
