"""Catalog build — Rust-backed core with Python-only tool entry helpers."""

from __future__ import annotations

import copy
from typing import Any

from cyt_indexer.build import (
    CatalogIndex,
    build_catalog_index,
    catalog_tool_count,
    collect_enums,
)

__all__ = [
    "CatalogIndex",
    "build_catalog_index",
    "catalog_tool_count",
    "collect_enums",
    "prepare_system_tool_entry",
    "prepare_tool_entry",
]


def truncate_description(description: str | None, max_tokens: int = 60) -> str:
    if not description:
        return ""
    max_chars = max_tokens * 4
    if len(description) <= max_chars:
        return description
    return description[:max_chars].rsplit(" ", 1)[0] + "..."


def prepare_tool_entry(server_name: str, tool: Any) -> dict[str, Any]:
    """Build one non-system (mcp__ id) or generic tool catalog entry without file I/O."""
    tool_id: str = tool.name

    input_schema = copy.deepcopy(tool.inputSchema)
    full_schema = {
        "id": tool_id,
        "name": tool_id,
        "description": tool.description,
        "inputSchema": input_schema,
    }

    return {
        "id": tool_id,
        "server": server_name,
        "tool": tool_id,
        "summary": truncate_description(tool.description or ""),
        "full_schema": full_schema,
    }


def prepare_system_tool_entry(tool: Any) -> dict[str, Any]:
    """Build a system tool entry (id does not use mcp__ prefix)."""
    return prepare_tool_entry("", tool)
