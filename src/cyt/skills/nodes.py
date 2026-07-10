"""Shared helpers for decomposed skill node loading from the entries cache."""

from __future__ import annotations

import logging
import os
from typing import Any

from cyt.common.paths import shorten_home_path
from cyt.indexer.pageindex import get_skill_line_content
from cyt.skills.catalog import SkillEntryRef, _iter_content_node_ids
from cyt.skills.frontmatter import skill_name_from_frontmatter

logger = logging.getLogger(__name__)


def _native_fallback_enabled() -> bool:
    return os.environ.get("CYT_DEBUG_NATIVE_FALLBACK") == "1"


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
    body, _token_count = load_node_content(entry, node_id)
    return body


def load_node_content(entry: SkillEntryRef, node_id: int) -> tuple[str, int | None]:
    index = entry.memory_index if not entry.disk_backed else None
    if index is None and entry.disk_backed:
        from cyt.skills.catalog import load_entry_skills_index

        index = load_entry_skills_index(entry)
    if index is None:
        return "", None
    rows = get_skill_line_content(index, entry.doc_id, node_id_specs=[str(node_id)])
    if not rows:
        return "", None
    row = rows[0]
    content = row.get("content")
    body = str(content).strip() if content is not None else ""
    token_count = row.get("token_count")
    if token_count is None:
        return body, None
    try:
        parsed = int(token_count)
    except (TypeError, ValueError):
        return body, None
    return body, parsed if parsed > 0 else None


def _entries_payload_for_nodes(entries: list[SkillEntryRef]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for entry in entries:
        payload.append(
            {
                "entry_dir": entry.entry_dir,
                "doc_id": entry.doc_id,
                "source_path": shorten_home_path(entry.source_path),
                "document": entry.document,
                "bm25_chunk_dir": entry.bm25_chunk_dir,
            },
        )
    return payload


def build_skill_node_items(entries: list[SkillEntryRef]) -> list[dict[str, Any]]:
    """Build rerankable items from cached content nodes (never chunks)."""
    from cyt.indexer.pipeline import build_skill_node_catalog

    try:
        items = build_skill_node_catalog(_entries_payload_for_nodes(entries))
        if items:
            return items
    except Exception:
        logger.exception("build_skill_node_catalog native call failed")
        if not _native_fallback_enabled():
            raise

    if not _native_fallback_enabled():
        return []

    fallback_items: list[dict[str, Any]] = []
    for entry in entries:
        structure = entry.document.get("structure")
        if not structure:
            continue
        file_path = shorten_home_path(entry.source_path)
        for node_id in _iter_content_node_ids(structure):
            body = load_node_body(entry, node_id)
            if not body:
                continue
            fallback_items.append(
                {
                    "entry_dir": entry.entry_dir,
                    "doc_id": entry.doc_id,
                    "node_id": node_id,
                    "file_path": file_path,
                    "content": body,
                    "score": 0.0,
                },
            )
    return fallback_items
