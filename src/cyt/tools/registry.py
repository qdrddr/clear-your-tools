"""Unified tool catalog loading for hook injection."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cyt.config import (
    load_config,
    resolved_tools_hook_file,
    tools_hook_file_missing,
    tools_hook_tools_from,
)
from cyt.tools.sources.definitions import load_definitions_file
from cyt.tools.sources.mcp_client import load_mcp_client_tools

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 60.0


@dataclass(frozen=True)
class _DefinitionsCacheKey:
    path: str
    mtime_ns: int


_definitions_cache: dict[_DefinitionsCacheKey, tuple[float, list[dict[str, Any]]]] = {}


def load_tool_catalog(config: dict[str, Any] | None = None) -> list[dict[str, Any]] | None:
    """Load the tool catalog for hook pruning.

    Returns None when the resolved catalog file is missing (graceful no-op).
    """
    cfg = config or load_config()
    if tools_hook_file_missing(cfg):
        return None

    path = resolved_tools_hook_file(cfg)
    if tools_hook_tools_from(cfg) == "definitions":
        return _load_definitions_cached(path)
    try:
        return load_mcp_client_tools(path, claude_fallback=True)
    except ImportError:
        logger.debug("fastmcp not installed; skipping MCP client tool catalog")
        return None


def _load_definitions_cached(path: Path) -> list[dict[str, Any]]:
    resolved = path.expanduser()
    mtime_ns = resolved.stat().st_mtime_ns
    key = _DefinitionsCacheKey(str(resolved), mtime_ns)
    now = time.monotonic()
    cached = _definitions_cache.get(key)
    if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    tools = load_definitions_file(resolved)
    _definitions_cache[key] = (now, tools)
    return tools
