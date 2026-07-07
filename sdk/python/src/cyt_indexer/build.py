"""Catalog build — Rust-backed."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cyt_indexer._native import NativeCatalogIndex
from cyt_indexer._native import anthropic_tool_to_catalog_entry as _anthropic_tool_to_catalog_entry
from cyt_indexer._native import anthropic_tools_to_catalog_entries as _anthropic_tools_to_catalog_entries
from cyt_indexer._native import build_catalog_from_tools as _build_catalog_from_tools
from cyt_indexer._native import build_catalog_index as _build_catalog_index
from cyt_indexer._native import catalog_index_to_catalog_dict as _catalog_index_to_catalog_dict
from cyt_indexer._native import catalog_tool_count as _catalog_tool_count
from cyt_indexer._native import prepare_tool_entry as _prepare_tool_entry
from cyt_indexer._native import truncate_description
from cyt_indexer.paths import collect_enums

__all__ = [
    "CatalogIndex",
    "NativeCatalogIndex",
    "anthropic_tool_to_catalog_entry",
    "anthropic_tools_to_catalog_entries",
    "build_catalog_from_tools",
    "build_catalog_index",
    "catalog_tool_count",
    "collect_enums",
    "prepare_tool_entry",
    "truncate_description",
]


def catalog_tool_count(data: dict[str, Any]) -> int:
    """Return the number of tools represented in a decomposed catalog dict."""
    return _catalog_tool_count(data)


@dataclass
class CatalogIndex:
    """In-memory catalog index: tool metadata plus generated file contents."""

    tools: list[dict[str, Any]]
    files: dict[str, str] = field(default_factory=dict)
    _native: NativeCatalogIndex | None = field(default=None, repr=False, compare=False)

    def native_handle(self) -> NativeCatalogIndex:
        """Return a Rust-backed handle to avoid re-serializing this index across FFI calls."""
        if self._native is None:
            self._native = NativeCatalogIndex.from_parts(self.tools, self.files)
        return self._native

    @classmethod
    def from_native(cls, native: NativeCatalogIndex) -> CatalogIndex:
        return cls(
            tools=list(native.tools),
            files=dict(native.files),
            _native=native,
        )

    def to_catalog_dict(
        self,
        catalog_prefix: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Convert decomposed catalog files to rerank/llm input format (Rust-backed)."""
        return _catalog_index_to_catalog_dict(
            self.native_handle(),
            catalog_prefix,
        )


def _catalog_index_from_raw(raw: dict[str, Any]) -> CatalogIndex:
    return CatalogIndex(tools=list(raw["tools"]), files=dict(raw["files"]))


def build_catalog_index(
    tools: list[dict[str, Any]],
    all_enums: list[Any],
) -> CatalogIndex:
    """Build the full catalog index in memory without writing to disk."""
    raw = _build_catalog_index(tools, all_enums)
    return _catalog_index_from_raw(raw)


def build_catalog_from_tools(tools: list[dict[str, Any]]) -> CatalogIndex:
    """Build catalog from Anthropic API tools and/or pre-built catalog entries."""
    raw = _build_catalog_from_tools(tools)
    return _catalog_index_from_raw(raw)


def prepare_tool_entry(
    server_name: str,
    name: str,
    description: str,
    input_schema: dict[str, Any],
) -> dict[str, Any]:
    """Build one catalog entry without file I/O (Rust-backed)."""
    return _prepare_tool_entry(server_name, name, description, input_schema)


def anthropic_tool_to_catalog_entry(tool: dict[str, Any]) -> dict[str, Any] | None:
    """Convert one Anthropic ``{name, input_schema, ...}`` tool to a catalog entry."""
    return _anthropic_tool_to_catalog_entry(tool)


def anthropic_tools_to_catalog_entries(
    tools: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[Any]]:
    """Convert Anthropic API tools to catalog entries and enum values."""
    raw = _anthropic_tools_to_catalog_entries(tools)
    return list(raw["entries"]), list(raw["enums"])
