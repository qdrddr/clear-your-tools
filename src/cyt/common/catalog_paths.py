"""Shared decomposed catalog path helpers (no imports from build/retrieve modules)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

JSON_EXT = ".json"
MD_EXT = ".md"
DECOMPOSED_PREFIX = "schemas/decomposed/"
DECOMPOSED_ROOT = Path("schemas/decomposed")


def to_decomposed_key(file_path: str) -> str | None:
    """Normalize a file path to schemas/decomposed/... form."""
    parts = Path(file_path).parts
    for i in range(len(parts) - 1):
        if parts[i] == "schemas" and parts[i + 1] == "decomposed":
            return str(Path(*parts[i:]))
    return None


def tool_id_from_decomposed_rel(rel_path: str) -> str:
    """Return the tool id encoded in a decomposed catalog relative path."""
    if rel_path.startswith(DECOMPOSED_PREFIX):
        rel = rel_path[len(DECOMPOSED_PREFIX) :]
    else:
        rel = rel_path
    parts = Path(rel).parts
    if not parts:
        return Path(rel).stem
    first = parts[0]
    if first.endswith(JSON_EXT):
        return first[: -len(JSON_EXT)]
    return first


def get_root_tool_key(file_path: str) -> str | None:
    """Given any decomposed file path, return its root tool file key."""
    key = to_decomposed_key(file_path)
    if key is None:
        return None

    rel = Path(key).relative_to(DECOMPOSED_ROOT)
    if not rel.parts:
        return None

    if len(rel.parts) == 1 and rel.parts[0].endswith(JSON_EXT):
        return key

    tool_id = rel.parts[0]
    return str(DECOMPOSED_ROOT / f"{tool_id}{JSON_EXT}")


def collect_enums(schema: Any) -> list[Any]:
    """Walk a JSON schema and return all enum values found."""
    found: list[Any] = []
    if isinstance(schema, dict):
        if "enum" in schema and isinstance(schema["enum"], list):
            found.extend(schema["enum"])
        for val in schema.values():
            if isinstance(val, dict | list):
                found.extend(collect_enums(val))
    elif isinstance(schema, list):
        for item in schema:
            if isinstance(item, dict | list):
                found.extend(collect_enums(item))
    return found
