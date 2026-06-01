"""Catalog build — Rust-backed."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cyt_indexer._native import build_catalog_index as _build_catalog_index
from cyt_indexer._native import catalog_tool_count as _catalog_tool_count
from cyt_indexer.paths import (
    DECOMPOSED_PREFIX,
    JSON_EXT,
    MD_EXT,
    collect_enums,
    tool_id_from_decomposed_rel,
)

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
        catalog_prefix: str = "src/catalog",
    ) -> dict[str, list[dict[str, Any]]]:
        """Convert decomposed catalog files to rerank/llm input format."""
        md_entries: list[dict[str, Any]] = []
        json_entries: list[dict[str, Any]] = []

        for rel_path, content in sorted(self.files.items()):
            if not rel_path.startswith(DECOMPOSED_PREFIX):
                continue

            file_path = f"{catalog_prefix}/{rel_path}"
            suffix = Path(rel_path).suffix.lower()

            if suffix == MD_EXT:
                md_entries.append(
                    {
                        "id": Path(rel_path).stem,
                        "file_path": file_path,
                        "score": 1.0,
                        "start_line": 1,
                        "end_line": 1,
                        "language": "markdown",
                        "content": content,
                    },
                )
            elif suffix == JSON_EXT:
                parsed = json.loads(content)
                line_count = len(content.splitlines())
                entry_id = parsed.get("id") or tool_id_from_decomposed_rel(rel_path)
                json_entries.append(
                    {
                        "id": entry_id,
                        "name": entry_id,
                        "file_path": file_path,
                        "score": 1.0,
                        "start_line": 1,
                        "end_line": line_count,
                        "language": "json",
                        "content": parsed,
                    },
                )

        return {"md": md_entries, "json": json_entries, "tools": self.tools}


def build_catalog_index(
    tools: list[dict[str, Any]],
    all_enums: list[Any],
) -> CatalogIndex:
    """Build the full catalog index in memory without writing to disk."""
    raw = _build_catalog_index(tools, all_enums)
    return CatalogIndex(tools=list(raw["tools"]), files=dict(raw["files"]))
