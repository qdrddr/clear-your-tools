"""Unified tool catalog loading for hook injection."""

from __future__ import annotations

from typing import Any

from cyt.config import load_config, tools_hook_file_missing, uses_cyt_mcp_tool_catalog
from cyt.tools.master_catalog import get_master_tool_catalog


def _force_rules_refresh_from_payload(payload: dict[str, Any] | None) -> bool:
    if payload is None:
        return False
    raw = payload.get("cyt_force_rules_refresh")
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().casefold() in {"1", "true", "yes", "on"}
    return False


def load_tool_catalog(
    config: dict[str, Any] | None = None,
    *,
    payload: dict[str, Any] | None = None,
) -> list[dict[str, Any]] | None:
    """Load the master hook tool catalog (SWR read; blocking when rules refresh is forced).

    Returns None when no configured source is usable (graceful no-op).
    """
    cfg = config or load_config()
    if tools_hook_file_missing(cfg):
        return None
    blocking = _force_rules_refresh_from_payload(payload)
    cold_start = False
    if not blocking:
        snapshot = get_master_tool_catalog(cfg, blocking=False)
        if (
            snapshot is not None
            and len(snapshot) == 0
            and uses_cyt_mcp_tool_catalog(cfg)
        ):
            blocking = True
            cold_start = True
    return get_master_tool_catalog(cfg, blocking=blocking, cold_start=cold_start)
