"""Catalog build — Rust-backed."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cyt_indexer._native import build_catalog_index as _build_catalog_index
from cyt_indexer._native import catalog_index_to_catalog_dict as _catalog_index_to_catalog_dict
from cyt_indexer._native import catalog_tool_count as _catalog_tool_count
from cyt_indexer.paths import collect_enums

__all__ = [
    "CatalogIndex",
    "build_catalog_index",
    "catalog_tool_count",
    "collect_enums",
]


def catalog_tool_count(data: dict[str, Any]) -> int:
    """Return the number of tools represented in a decomposed catalog dict."""
    return _catalog_tool_count(data)


@dataclass
class CatalogIndex:
    """In-memory catalog index: tool metadata plus generated file contents."""

    tools: list[dict[str, Any]]
    files: dict[str, str] = field(default_factory=dict)

    def to_catalog_dict(
        self,
        catalog_prefix: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Convert decomposed catalog files to rerank/llm input format (Rust-backed)."""
        return _catalog_index_to_catalog_dict(
            {"tools": self.tools, "files": self.files},
            catalog_prefix,
        )


def build_catalog_index(
    tools: list[dict[str, Any]],
    all_enums: list[Any],
) -> CatalogIndex:
    """Build the full catalog index in memory without writing to disk."""
    raw = _build_catalog_index(tools, all_enums)
    return CatalogIndex(tools=list(raw["tools"]), files=dict(raw["files"]))
