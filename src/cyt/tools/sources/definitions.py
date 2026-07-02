"""Load static tool catalogs from JSON definitions files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_definitions_file(path: Path) -> list[dict[str, Any]]:
    """Parse a definitions JSON file into Anthropic-style tool dicts."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        tools = raw
    elif isinstance(raw, dict):
        tools = raw.get("tools", [])
    else:
        raise ValueError(f"unsupported definitions root type: {type(raw).__name__}")

    if not isinstance(tools, list):
        raise ValueError("definitions file must contain a tools array")

    normalized: list[dict[str, Any]] = []
    for item in tools:
        if not isinstance(item, dict):
            continue
        tool = _normalize_definition_entry(item)
        if tool.get("name"):
            normalized.append(tool)
    return normalized


def _normalize_definition_entry(item: dict[str, Any]) -> dict[str, Any]:
    name = str(item.get("name") or item.get("id") or "").strip()
    description = item.get("description")
    if description is None and isinstance(item.get("full_schema"), dict):
        description = item["full_schema"].get("description")

    input_schema = item.get("input_schema")
    if input_schema is None and isinstance(item.get("full_schema"), dict):
        input_schema = item["full_schema"].get("input_schema")

    tool: dict[str, Any] = {"name": name}
    if description is not None:
        tool["description"] = str(description)
    if isinstance(input_schema, dict):
        tool["input_schema"] = input_schema
    return tool
