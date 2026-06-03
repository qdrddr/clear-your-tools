"""Catalog disk I/O and builder (Rust-backed)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cyt_indexer._native import CatalogBuilder as _NativeCatalogBuilder
from cyt_indexer._native import write_catalog_index as _write_catalog_index


def write_catalog_index(
    index: dict[str, Any],
    output_dir: str | Path | None = None,
    *,
    prune: bool | None = None,
) -> None:
    """Write a catalog index payload to disk (Rust defaults when args omitted)."""
    _write_catalog_index(
        index,
        str(output_dir) if output_dir is not None else None,
        prune,
    )


class CatalogBuilder:
    """Accumulates tool entries and writes decomposed catalog files."""

    def __init__(
        self,
        memory_only: bool | None = None,
        output_dir: Path | None = None,
    ) -> None:
        out = str(output_dir) if output_dir is not None else None
        self._inner = _NativeCatalogBuilder(memory_only, out)

    def add_tool(self, entry: dict[str, Any]) -> None:
        self._inner.add_tool(entry)

    def get_tool_info(self, server_name: str, tool_name: str) -> dict[str, Any] | None:
        return self._inner.get_tool_info(server_name, tool_name)

    def build_index(self) -> dict[str, Any]:
        return self._inner.build_index()

    def write_catalog(self) -> dict[str, Any]:
        return self._inner.write_catalog()

    def to_catalog_dict(
        self,
        catalog_prefix: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Return decomposed catalog in rerank/llm input format (Rust-backed)."""
        return self._inner.to_catalog_dict(catalog_prefix)
