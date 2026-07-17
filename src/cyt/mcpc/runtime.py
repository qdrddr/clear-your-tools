"""Injectable runtime hooks for MCPC host integration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

_load_config: Callable[[], dict[str, Any]] | None = None
_connection_health_flapping_settings: Callable[[dict[str, Any] | None], dict[str, Any]] | None = (
    None
)
_tools_hook_mcpc_cache_settings: Callable[[dict[str, Any] | None], dict[str, Any]] | None = None
_tools_hook_mcpc_executable: Callable[[dict[str, Any] | None], str] | None = None
_uses_mcpc_tool_catalog: Callable[[dict[str, Any] | None], bool] | None = None


def configure_runtime(
    *,
    load_config: Callable[[], dict[str, Any]],
    connection_health_flapping_settings: Callable[[dict[str, Any] | None], dict[str, Any]],
    tools_hook_mcpc_cache_settings: Callable[[dict[str, Any] | None], dict[str, Any]],
    tools_hook_mcpc_executable: Callable[[dict[str, Any] | None], str],
    uses_mcpc_tool_catalog: Callable[[dict[str, Any] | None], bool],
) -> None:
    global _load_config
    global _connection_health_flapping_settings
    global _tools_hook_mcpc_cache_settings
    global _tools_hook_mcpc_executable
    global _uses_mcpc_tool_catalog
    _load_config = load_config
    _connection_health_flapping_settings = connection_health_flapping_settings
    _tools_hook_mcpc_cache_settings = tools_hook_mcpc_cache_settings
    _tools_hook_mcpc_executable = tools_hook_mcpc_executable
    _uses_mcpc_tool_catalog = uses_mcpc_tool_catalog


def runtime_configured() -> bool:
    return _load_config is not None


def load_config() -> dict[str, Any]:
    if _load_config is None:
        return {}
    return _load_config()


def connection_health_flapping_settings(config: dict[str, Any] | None = None) -> dict[str, Any]:
    if _connection_health_flapping_settings is None:
        return {}
    return _connection_health_flapping_settings(config)


def tools_hook_mcpc_cache_settings(config: dict[str, Any] | None = None) -> dict[str, Any]:
    if _tools_hook_mcpc_cache_settings is None:
        return {
            "session_refresh_seconds": 1,
            "tools_refresh_seconds": 120,
            "disk_flush_seconds": 900,
        }
    return _tools_hook_mcpc_cache_settings(config)


def tools_hook_mcpc_executable(config: dict[str, Any] | None = None) -> str:
    if _tools_hook_mcpc_executable is None:
        return "mcpc"
    return _tools_hook_mcpc_executable(config)


def uses_mcpc_tool_catalog(config: dict[str, Any] | None = None) -> bool:
    if _uses_mcpc_tool_catalog is None:
        return False
    return _uses_mcpc_tool_catalog(config)
