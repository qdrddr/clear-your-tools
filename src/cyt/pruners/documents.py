"""Extract searchable text from decomposed catalog chunks (Rust-backed)."""

from typing import Any

from cyt_indexer.documents import (
    extract_document_text,
    extract_json_catalog_document,
    extract_md_catalog_document,
)


def extract_skill_node_document(item: dict[str, Any]) -> str | None:
    """Return searchable text from a decomposed skill node item."""
    content = item.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    return None


__all__ = [
    "extract_document_text",
    "extract_json_catalog_document",
    "extract_md_catalog_document",
    "extract_skill_node_document",
]
