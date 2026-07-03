"""Shared helpers for decomposed skill node loading from the entries cache."""

from __future__ import annotations

from typing import Any

from cyt_indexer import get_skill_line_content

from cyt.skills.catalog import SkillEntryRef, _iter_content_node_ids, _shorten_home_path
from cyt.skills.frontmatter import skill_name_from_frontmatter


def count_content_nodes(entries: list[SkillEntryRef]) -> int:
    """Count content nodes across entries using structure metadata only (no disk I/O)."""
    total = 0
    for entry in entries:
        structure = entry.document.get("structure")
        if structure:
            total += len(_iter_content_node_ids(structure))
    return total


def skill_name(entry: SkillEntryRef) -> str | None:
    raw = entry.document.get("frontmatter")
    frontmatter = raw if isinstance(raw, str) else None
    return skill_name_from_frontmatter(frontmatter)


def load_node_body(entry: SkillEntryRef, node_id: int) -> str:
    index = entry.memory_index if not entry.disk_backed else None
    if index is None and entry.disk_backed:
        from cyt.skills.catalog import load_entry_skills_index

        index = load_entry_skills_index(entry)
    if index is None:
        return ""
    rows = get_skill_line_content(index, entry.doc_id, node_id_specs=[str(node_id)])
    if not rows:
        return ""
    content = rows[0].get("content") if isinstance(rows[0], dict) else None
    return str(content).strip() if content is not None else ""


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
