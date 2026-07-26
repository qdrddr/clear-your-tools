"""Unified tool catalog loading for hook injection."""

from __future__ import annotations

from typing import Any

from cyt.config import load_config, tools_hook_file_missing
from cyt.tools.master_catalog import get_master_tool_catalog


def load_tool_catalog(config: dict[str, Any] | None = None) -> list[dict[str, Any]] | None:
    """Load the master hook tool catalog (non-blocking SWR read).

    Returns None when no configured source is usable (graceful no-op).
    """
    cfg = config or load_config()
    if tools_hook_file_missing(cfg):
        return None
    return get_master_tool_catalog(cfg, blocking=False)
