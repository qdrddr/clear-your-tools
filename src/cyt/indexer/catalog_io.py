"""Disk-backed catalog index I/O and builder."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from cyt_indexer.catalog_io import CatalogBuilder as _NativeCatalogBuilder
from cyt_indexer.catalog_io import write_catalog_index as _write_catalog_index

from cyt.common.path_constants import DEFAULT_CATALOG_DIR
from cyt.indexer.build import CatalogIndex, ToolSchemaSource, prepare_tool_entry

logger = logging.getLogger(__name__)


def write_catalog_index(
    index: CatalogIndex,
    output_dir: Path | None = None,
    *,
    prune: bool | None = None,
) -> None:
    """Write a CatalogIndex to disk (Rust-backed)."""
    root = output_dir or DEFAULT_CATALOG_DIR
    payload = {"tools": index.tools, "files": index.files}
    _write_catalog_index(payload, root, prune=prune)


class CatalogBuilder:
    """Handles creation and writing of the tool catalog / index."""

    def __init__(
        self,
        memory_only: bool | None = None,
        output_dir: Path | None = None,
    ) -> None:
        self.memory_only = memory_only
        self.output_dir = output_dir
        self._inner = _NativeCatalogBuilder(memory_only, output_dir)

    def prepare_tool(self, server_name: str, tool: ToolSchemaSource) -> str:
        """Process a discovered tool for the catalog; return its catalog id."""
        entry = prepare_tool_entry(server_name, tool)
        self._inner.add_tool(entry)
        return str(entry["id"])

    def get_tool_info(self, server_name: str, tool_name: str) -> dict[str, Any] | None:
        return self._inner.get_tool_info(server_name, tool_name)

    def build_index(self) -> CatalogIndex:
        raw = self._inner.build_index()
        return CatalogIndex(tools=raw["tools"], files=raw["files"])

    def write_catalog(self) -> CatalogIndex:
        if self.memory_only:
            return self.build_index()
        raw = self._inner.write_catalog()
        return CatalogIndex(tools=raw["tools"], files=raw["files"])

    def to_catalog_dict(
        self,
        catalog_prefix: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Return decomposed catalog in rerank/llm input format."""
        return self._inner.to_catalog_dict(catalog_prefix)
