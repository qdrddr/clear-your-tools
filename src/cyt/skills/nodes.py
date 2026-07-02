"""Shared helpers for decomposed skill node loading from the entries cache."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cyt.skills.bm25 import _strip_frontmatter
from cyt.skills.catalog import SkillEntryRef, _iter_content_node_ids, _shorten_home_path
from cyt.skills.frontmatter import skill_name_from_frontmatter


def skill_name(entry: SkillEntryRef) -> str | None:
    raw = entry.document.get("frontmatter")
    frontmatter = raw if isinstance(raw, str) else None
    return skill_name_from_frontmatter(frontmatter)


def load_node_body(entry: SkillEntryRef, node_id: int) -> str:
    if not entry.disk_backed and entry.memory_index is not None:
        files = entry.memory_index.get("files")
        if isinstance(files, dict):
            rel = f"nodes/n{node_id}.md"
            raw = files.get(rel)
            if isinstance(raw, str):
                return _strip_frontmatter(raw).strip()
    node_path = Path(entry.nodes_dir) / f"n{node_id}.md"
    if not node_path.is_file():
        return ""
    return _strip_frontmatter(node_path.read_text(encoding="utf-8")).strip()


def build_skill_node_items(entries: list[SkillEntryRef]) -> list[dict[str, Any]]:
    """Build rerankable items from cached content nodes (never chunks)."""
    items: list[dict[str, Any]] = []
    for entry in entries:
        structure = entry.document.get("structure")
        if not structure:
            continue
        file_path = _shorten_home_path(entry.source_path)
        for node_id in _iter_content_node_ids(structure):
            body = load_node_body(entry, node_id)
            if not body:
                continue
            items.append(
                {
                    "entry_dir": entry.entry_dir,
                    "doc_id": entry.doc_id,
                    "node_id": node_id,
                    "file_path": file_path,
                    "content": body,
                    "score": 0.0,
                },
            )
    return items
