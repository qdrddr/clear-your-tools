"""Document text extraction for catalog chunks (Rust-backed)."""

from __future__ import annotations

from typing import Any

import cyt_indexer._native as _native


def extract_json_catalog_document(item: dict[str, Any]) -> str | None:
    return _native.extract_json_catalog_document(item)


def extract_md_catalog_document(item: dict[str, Any]) -> str | None:
    return _native.extract_md_catalog_document(item)


def extract_document_text(item_content: dict[str, Any]) -> str | None:
    return _native.extract_document_text(item_content)


def extract_level_info(data: dict[str, Any]) -> list[str]:
    return list(_native.extract_level_info(data))
