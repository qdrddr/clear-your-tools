"""Extract searchable text from decomposed catalog chunks."""

from __future__ import annotations

from typing import Any


def extract_level_info(data: Any) -> list[str]:
    """
    Recursively searches for description, default, and enum keys at all levels.
    Returns a list of formatted strings, one for each level where at least a description is found.
    """
    results: list[str] = []

    if isinstance(data, dict):
        desc = data.get("description")
        default_val = data.get("default")
        enums = data.get("enum")

        if desc:
            line = str(desc)
            if default_val is not None:
                line += f"; Default: {default_val}"
            if enums and isinstance(enums, list):
                enums_str = ", ".join(map(str, enums))
                line += f"; Options: {enums_str}"
            results.append(line)

        for val in data.values():
            results.extend(extract_level_info(val))

    elif isinstance(data, list):
        for item in data:
            results.extend(extract_level_info(item))

    return results


def extract_document_text(item_content: Any) -> str | None:
    """Combine description/default/enum lines from schema content, one per line."""
    level_lines = extract_level_info(item_content)
    if not level_lines:
        return None
    return "\n".join(level_lines)


def extract_json_catalog_document(item: dict[str, Any]) -> str | None:
    """Build document text from schema content only (exclude catalog metadata like id)."""
    content = item.get("content")
    if content is None:
        return None
    return extract_document_text(content)


def extract_md_catalog_document(item: dict[str, Any]) -> str | None:
    content = item.get("content")
    return str(content) if content else None
