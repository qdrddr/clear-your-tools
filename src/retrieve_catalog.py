"""Reconstruct tool schemas from decomposed catalog data (in-memory)."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from build_index import CatalogIndex

JSON_EXT = ".json"
DECOMPOSED_ROOT = Path("schemas/decomposed")

DECOMPOSED_SCORE: float = 0.5
ENUM_SCORE: float = 0.2


def to_decomposed_key(file_path: str) -> str | None:
    """Normalize a file path to schemas/decomposed/... form."""
    parts = Path(file_path).parts
    for i in range(len(parts) - 1):
        if parts[i] == "schemas" and parts[i + 1] == "decomposed":
            return str(Path(*parts[i:]))
    return None


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


@dataclass
class DecomposedCatalog:
    """In-memory access to decomposed catalog JSON files."""

    _json_files: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_catalog_index(cls, index: CatalogIndex) -> DecomposedCatalog:
        json_files: dict[str, dict[str, Any]] = {}
        for rel_path, content in index.files.items():
            if rel_path.startswith("schemas/decomposed/") and rel_path.endswith(JSON_EXT):
                json_files[rel_path] = json.loads(content)
        return cls(_json_files=json_files)

    @classmethod
    def from_catalog_dict(cls, data: dict[str, Any]) -> DecomposedCatalog:
        """Build from cs.py / rerank.py / llm.py catalog dict format."""
        json_files: dict[str, dict[str, Any]] = {}
        for entry in data.get("json", []):
            if not isinstance(entry, dict):
                continue
            file_path = entry.get("file_path")
            content = entry.get("content")
            if not isinstance(file_path, str) or not isinstance(content, dict):
                continue
            key = to_decomposed_key(file_path)
            if key is not None:
                json_files[key] = content
        return cls(_json_files=json_files)

    def resolve_key(self, file_path: str) -> str | None:
        """Resolve an external path to an internal decomposed key, if present."""
        candidates: list[str] = []
        normalized = to_decomposed_key(file_path)
        if normalized is not None:
            candidates.append(normalized)
        candidates.append(file_path)

        for candidate in candidates:
            if self.has_json(candidate):
                return candidate
        return None

    def has_json(self, key: str) -> bool:
        return key in self._json_files

    def get_json(self, key: str) -> dict[str, Any] | None:
        return self._json_files.get(key)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override into base. Dicts are merged; other values are overwritten."""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def climb_and_merge(leaf_path: Path | str, catalog: DecomposedCatalog) -> dict[str, Any]:
    """Merge a surviving optional leaf up through ancestor property files in ``catalog``.

    Called for every optional leaf that passed ``optional_leaf_survived_rerank``. Ancestors
    present in the survivor store are merged in; missing ancestors are skipped (the leaf
    file already embeds its decomposition path).
    """
    leaf_key = catalog.resolve_key(str(leaf_path)) if not isinstance(leaf_path, str) else leaf_path
    if leaf_key is None:
        leaf_key = to_decomposed_key(str(leaf_path)) or str(leaf_path)

    current = catalog.get_json(leaf_key)
    if current is None:
        return {}

    current_path = Path(leaf_key).parent

    while True:
        parent_dir = current_path.parent
        if parent_dir == DECOMPOSED_ROOT or not str(parent_dir).startswith(str(DECOMPOSED_ROOT)):
            break

        parent_key = str(parent_dir / f"{current_path.name}{JSON_EXT}")
        parent = catalog.get_json(parent_key)
        if parent is not None:
            current = deep_merge(parent, current)
            current_path = parent_dir
        else:
            current_path = parent_dir

    return current


def _extract_scores(data: Any) -> dict[str, float]:
    """Extract scores from the 'md' and 'json' root keys."""
    scores: dict[str, float] = {}
    if not isinstance(data, dict):
        return scores
    if "md" in data:
        for entry in data["md"]:
            if isinstance(entry, dict) and "content" in entry and "score" in entry:
                scores[entry["content"]] = float(entry["score"])
    if "json" in data:
        for entry in data["json"]:
            if isinstance(entry, dict) and "file_path" in entry and "score" in entry:
                scores[entry["file_path"]] = float(entry["score"])
    return scores


def _extract_from_dict(
    data: dict[str, Any],
    *,
    apply_decomposed_score_filter: bool = True,
) -> list[str]:
    """Helper to extract file paths from a dictionary."""
    input_files: list[str] = []
    for key, value in data.items():
        if key == "md":
            continue
        if isinstance(value, list):
            for entry in value:
                if isinstance(entry, dict) and "file_path" in entry:
                    if key == "json" and apply_decomposed_score_filter:
                        score = float(entry.get("score", 0))
                        if score <= DECOMPOSED_SCORE:
                            continue
                    input_files.append(entry["file_path"])
        elif isinstance(value, dict) and "file_path" in value:
            input_files.append(value["file_path"])
    return input_files


def _extract_input_files(
    data: Any,
    *,
    apply_decomposed_score_filter: bool = True,
) -> list[str]:
    """Extract file paths from various JSON formats."""
    if isinstance(data, dict):
        return _extract_from_dict(data, apply_decomposed_score_filter=apply_decomposed_score_filter)
    if isinstance(data, list):
        return [
            entry["file_path"] for entry in data if isinstance(entry, dict) and "file_path" in entry
        ]
    return []


def parse_json_input(
    data: Any,
    *,
    apply_decomposed_score_filter: bool = True,
) -> tuple[list[str], dict[str, float]]:
    """Extract file paths and scores from various JSON formats."""
    return (
        _extract_input_files(data, apply_decomposed_score_filter=apply_decomposed_score_filter),
        _extract_scores(data),
    )


def _filter_items(items_with_scores: list[tuple[Any, float]]) -> list[Any]:
    """Apply the filtering logic to the sorted items."""
    first_3_above_threshold = True
    for i in range(min(3, len(items_with_scores))):
        if items_with_scores[i][1] < ENUM_SCORE:
            first_3_above_threshold = False
            break

    if first_3_above_threshold:
        return [item for item, score in items_with_scores if score >= ENUM_SCORE]
    return [item for item, score in items_with_scores[:3]]


def filter_and_sort_enums(
    schema: Any,
    scores: dict[str, float],
    preserve_values: frozenset[str] | None = None,
) -> None:
    """Recursively find 'enum' arrays in schema and apply filtering/sorting by score."""
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key == "enum" and isinstance(value, list):
                preserved: list[Any] = []
                prunable: list[Any] = []
                for item in value:
                    if preserve_values and str(item) in preserve_values:
                        preserved.append(item)
                    else:
                        prunable.append(item)
                items_with_scores = [(item, scores.get(str(item), 0.0)) for item in prunable]
                items_with_scores.sort(key=lambda x: x[1], reverse=True)
                schema[key] = preserved + _filter_items(items_with_scores)
            else:
                filter_and_sort_enums(value, scores, preserve_values=preserve_values)
    elif isinstance(schema, list):
        for item in schema:
            filter_and_sort_enums(item, scores, preserve_values=preserve_values)


def group_files(
    input_files: list[str],
    catalog: DecomposedCatalog,
) -> tuple[dict[str, list[str]], set[str]]:
    """Group input files by their root tool and identify standalone tool files."""
    groups: dict[str, list[str]] = {}
    tool_files: set[str] = set()

    for file_path in input_files:
        key = catalog.resolve_key(file_path)
        if key is None:
            print(f"Warning: File not found: {file_path}", file=sys.stderr)
            continue

        rel = Path(key).relative_to(DECOMPOSED_ROOT)
        parts = rel.parts
        is_tool = len(parts) == 1 and parts[0].endswith(JSON_EXT)

        root_tool = get_root_tool_key(key)
        if root_tool is None:
            continue

        if is_tool:
            tool_files.add(key)

        groups.setdefault(root_tool, []).append(key)

    return groups, tool_files


def _tool_shell_from_root_key(root_tool: str) -> dict[str, Any]:
    return {
        "name": Path(root_tool).stem,
        "inputSchema": {"type": "object", "properties": {}},
    }


def process_groups(
    groups: dict[str, list[str]],
    tool_files: set[str],
    scores: dict[str, float],
    catalog: DecomposedCatalog,
    *,
    system_preserve: frozenset[str] | None = None,
    mcp_preserve: frozenset[str] | None = None,
    required_by_tool: dict[str, frozenset[str]] | None = None,
    system_policy: str = "prune_optional",
    mcp_policy: str = "prune_all",
) -> list[dict[str, Any]]:
    """Merge and return the resulting schemas for each tool group."""
    from tool_policies import effective_policy

    del system_policy, mcp_policy
    tools: list[dict[str, Any]] = []

    for root_tool, files in groups.items():
        base_tool = catalog.get_json(root_tool) or _tool_shell_from_root_key(root_tool)
        tool_name_in_schema = base_tool.get("name", Path(root_tool).stem)

        for file_key in files:
            if file_key in tool_files:
                continue
            base_tool = deep_merge(base_tool, climb_and_merge(file_key, catalog))

        tool_name = base_tool.get("name") or tool_name_in_schema or Path(root_tool).stem
        base_tool["name"] = tool_name
        base_tool.pop("id", None)

        if scores:
            enum_preserve: frozenset[str] | None = None
            if effective_policy(tool_name) == "prune_optional":
                if required_by_tool and tool_name in required_by_tool:
                    enum_preserve = required_by_tool[tool_name]
                elif system_preserve:
                    enum_preserve = system_preserve
                elif mcp_preserve:
                    enum_preserve = mcp_preserve
            filter_and_sort_enums(base_tool, scores, preserve_values=enum_preserve)

        tools.append(base_tool)

    return tools


def retrieve_tools(
    data: Any,
    *,
    catalog: DecomposedCatalog | CatalogIndex,
    apply_decomposed_score_filter: bool = True,
    preserve_values: frozenset[str] | None = None,
    system_policy: str | None = None,
    mcp_policy: str | None = None,
) -> list[dict[str, Any]]:
    """
    Reconstruct merged tool schemas from search/rerank/llm output.

    Requires an in-memory ``catalog`` (DecomposedCatalog or CatalogIndex).
    """
    from build_index import CatalogIndex
    from tool_policies import (
        MCP_TOOL_POLICY,
        SYSTEM_TOOL_POLICY,
        mcp_required_enum_values,
        required_enum_values_by_tool,
        system_required_enum_values,
    )

    if system_policy is None:
        system_policy = SYSTEM_TOOL_POLICY
    if mcp_policy is None:
        mcp_policy = MCP_TOOL_POLICY

    if isinstance(catalog, DecomposedCatalog):
        store = catalog
    elif isinstance(catalog, CatalogIndex):
        store = DecomposedCatalog.from_catalog_index(catalog)
    else:
        raise TypeError("catalog must be DecomposedCatalog or CatalogIndex")

    catalog_dict = data if isinstance(data, dict) else {}
    survivor_store = DecomposedCatalog.from_catalog_dict(catalog_dict)
    if survivor_store._json_files:
        # Keep the full decomposed catalog for climb_and_merge ancestors; overlay
        # LLM/rerank survivor chunk content on top of index files.
        store._json_files.update(survivor_store._json_files)

    input_files, scores = parse_json_input(
        data,
        apply_decomposed_score_filter=apply_decomposed_score_filter,
    )
    system_preserve = system_required_enum_values(catalog_dict)
    mcp_preserve = mcp_required_enum_values(catalog_dict)
    required_by_tool = required_enum_values_by_tool(catalog_dict)
    if preserve_values is not None and not system_preserve:
        system_preserve = preserve_values

    groups, tool_files = group_files(input_files, store)
    return process_groups(
        groups,
        tool_files,
        scores,
        store,
        system_preserve=system_preserve or None,
        mcp_preserve=mcp_preserve or None,
        required_by_tool=required_by_tool or None,
        system_policy=system_policy,
        mcp_policy=mcp_policy,
    )
