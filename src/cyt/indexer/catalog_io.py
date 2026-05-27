"""Disk-backed catalog index I/O and builder."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from cyt.common.catalog_paths import collect_enums
from cyt.indexer.build import CatalogIndex, build_catalog_index, prepare_tool_entry

logger = logging.getLogger(__name__)

DEFAULT_CATALOG_DIR = Path("catalog")


def _apply_outputs(output_map: dict[Path, str]) -> None:
    """Idempotently write all collected files to disk."""
    for path, content in output_map.items():
        if path.exists():
            try:
                if path.read_text(encoding="utf-8") == content:
                    continue
            except OSError:
                pass
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _prune_stale_files(root: Path, expected_paths: set[Path]) -> None:
    """Remove files in root that are not in expected_paths, and empty dirs."""
    if not root.exists():
        return
    for path in root.rglob("*"):
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        if path.is_file() and path.absolute() not in expected_paths:
            path.unlink()
    for path in sorted(root.rglob("*"), key=lambda item: len(str(item)), reverse=True):
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()


def write_catalog_index(
    index: CatalogIndex,
    output_dir: Path | None = None,
    *,
    prune: bool = True,
) -> None:
    """Write a CatalogIndex to disk."""
    root = output_dir or DEFAULT_CATALOG_DIR
    root.mkdir(exist_ok=True, parents=True)
    (root / "schemas").mkdir(exist_ok=True, parents=True)

    output_map: dict[Path, str] = {}
    for rel_path, content in index.files.items():
        output_map[(root / rel_path).absolute()] = content

    _apply_outputs(output_map)
    if prune:
        _prune_stale_files(root, set(output_map.keys()))


class CatalogBuilder:
    """Handles creation and writing of the tool catalog / index."""

    def __init__(self, memory_only: bool = False, output_dir: Path | None = None) -> None:
        self.memory_only = memory_only
        self.output_dir = output_dir
        self.discovered_tools: list[dict[str, Any]] = []
        self.all_enums: list[Any] = []
        self._index: CatalogIndex | None = None

    def prepare_tool(self, server_name: str, tool: Any) -> str:
        """Process a discovered tool for the catalog; return its catalog id."""
        entry = prepare_tool_entry(server_name, tool)
        self.all_enums.extend(collect_enums(entry["full_schema"]["inputSchema"]))
        self.discovered_tools.append(entry)
        self._index = None
        return str(entry["id"])

    def get_tool_info(self, server_name: str, tool_name: str) -> dict[str, Any] | None:
        """Look up catalog entry for a given server/tool pair."""
        for tool in self.discovered_tools:
            if tool["server"] == server_name and tool["tool"] == tool_name:
                return tool
        return None

    def build_index(self) -> CatalogIndex:
        """Build the catalog index in memory."""
        self._index = build_catalog_index(self.discovered_tools, self.all_enums)
        return self._index

    def write_catalog(self) -> CatalogIndex:
        """Build the catalog index and write it to disk unless memory_only is set."""
        index = self.build_index()
        if not self.memory_only:
            write_catalog_index(index, output_dir=self.output_dir)
        return index

    def to_catalog_dict(
        self,
        catalog_prefix: str = "catalog",
    ) -> dict[str, list[dict[str, Any]]]:
        """Return decomposed catalog in rerank/llm input format."""
        index = self._index or self.build_index()
        return index.to_catalog_dict(catalog_prefix)
