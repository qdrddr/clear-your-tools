"""Catalog build — Rust-backed core with Python-only tool entry helpers."""

from __future__ import annotations

import copy
from typing import Any, Protocol

from cyt_indexer.build import (
    CatalogIndex,
    build_catalog_index,
    catalog_tool_count,
    collect_enums,
)

from cyt.indexer.tokens import truncate_description

__all__ = [
    "CatalogIndex",
    "ToolSchemaSource",
    "build_catalog_index",
    "catalog_tool_count",
    "collect_enums",
    "prepare_system_tool_entry",
    "prepare_tool_entry",
    "truncate_description",
]


class ToolSchemaSource(Protocol):
    """MCP / agent tool object with name, description, and JSON Schema input."""

    name: str
    description: str | None
    inputSchema: dict[str, Any]  # noqa: N815 — matches MCP tool objects


def prepare_tool_entry(server_name: str, tool: ToolSchemaSource) -> dict[str, Any]:
    """Build one non-system (mcp__ id) or generic tool catalog entry without file I/O."""
    tool_id = str(tool.name)
    description = tool.description
    input_schema = copy.deepcopy(tool.inputSchema)

    full_schema = {
        "id": tool_id,
        "name": tool_id,
        "description": description,
        "inputSchema": input_schema,
    }

    return {
        "id": tool_id,
        "server": server_name,
        "tool": tool_id,
        "summary": truncate_description(description or ""),
        "full_schema": full_schema,
    }


def prepare_system_tool_entry(tool: ToolSchemaSource) -> dict[str, Any]:
    """Build a system tool entry (id does not use mcp__ prefix)."""
    return prepare_tool_entry("", tool)
