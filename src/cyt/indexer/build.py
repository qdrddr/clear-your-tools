"""Catalog build — re-exports cyt-indexer-sdk with app-facing tool object helpers."""

from __future__ import annotations

from typing import Any, Protocol

from cyt_indexer.build import (
    CatalogIndex,
    anthropic_tool_to_catalog_entry,
    anthropic_tools_to_catalog_entries,
    build_catalog_from_tools,
    build_catalog_index,
    catalog_index_tool_schema_metadata,
    catalog_tool_count,
    collect_enums,
    truncate_description,
)
from cyt_indexer.build import (
    prepare_tool_entry as _prepare_tool_entry_flat,
)

__all__ = [
    "CatalogIndex",
    "ToolSchemaSource",
    "anthropic_tool_to_catalog_entry",
    "anthropic_tools_to_catalog_entries",
    "build_catalog_from_tools",
    "build_catalog_index",
    "catalog_index_tool_schema_metadata",
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
    """Build one catalog entry from a tool object (Rust-backed)."""
    return _prepare_tool_entry_flat(
        server_name,
        str(tool.name),
        tool.description or "",
        tool.inputSchema,
    )


def prepare_system_tool_entry(tool: ToolSchemaSource) -> dict[str, Any]:
    """Build a system tool entry (id does not use mcp__ prefix)."""
    return prepare_tool_entry("", tool)
