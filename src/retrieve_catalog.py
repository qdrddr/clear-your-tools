#!/usr/bin/env python3
"""Reconstruct tool schemas from decomposed property files."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from build_index import CatalogIndex

JSON_EXT = ".json"
MD_EXT = ".md"
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
    """Disk-backed or in-memory access to decomposed catalog JSON files."""

    decomposed_dir: Path | None = None
    _json_files: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_directory(cls, decomposed_dir: Path | str) -> DecomposedCatalog:
        return cls(decomposed_dir=Path(decomposed_dir))

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

        if self.decomposed_dir is not None:
            path = Path(file_path)
            if path.is_absolute():
                try:
                    rel = path.relative_to(self.decomposed_dir)
                    candidates.append(str(DECOMPOSED_ROOT / rel))
                except ValueError:
                    pass
            elif path.exists():
                try:
                    rel = path.relative_to(self.decomposed_dir)
                    candidates.append(str(DECOMPOSED_ROOT / rel))
                except ValueError:
                    pass

        for candidate in candidates:
            if self.has_json(candidate):
                return candidate
        return None

    def has_json(self, key: str) -> bool:
        if key in self._json_files:
            return True
        if self.decomposed_dir is not None:
            try:
                rel = Path(key).relative_to(DECOMPOSED_ROOT)
            except ValueError:
                return False
            return (self.decomposed_dir / rel).is_file()
        return False

    def get_json(self, key: str) -> dict[str, Any] | None:
        if key in self._json_files:
            return self._json_files[key]
        if self.decomposed_dir is not None:
            try:
                rel = Path(key).relative_to(DECOMPOSED_ROOT)
            except ValueError:
                return None
            path = self.decomposed_dir / rel
            if not path.is_file():
                return None
            with path.open(encoding="utf-8") as f:
                loaded: Any = json.load(f)
                if isinstance(loaded, dict):
                    return loaded
        return None


def load_catalog(dir_path: str) -> dict[str, list[dict[str, Any]]]:
    """
    Recursively walk the directory, read every *.json and *.md file,
    and build a dictionary structure matching the input for rerank/llm.
    """
    from build_index import tool_id_from_decomposed_rel

    root = Path(dir_path)
    if not root.is_dir():
        raise FileNotFoundError(f"Directory not found: {dir_path}")

    md_entries: list[dict[str, Any]] = []
    json_entries: list[dict[str, Any]] = []

    for file_path in root.rglob("*"):
        if not file_path.is_file():
            continue

        rel_path = str(file_path)
        suffix = file_path.suffix.lower()

        if suffix == MD_EXT:
            try:
                content = file_path.read_text(encoding="utf-8")
                md_entries.append(
                    {
                        "id": file_path.stem,
                        "file_path": rel_path,
                        "score": 0.0,
                        "start_line": 1,
                        "end_line": 1,
                        "language": "markdown",
                        "content": content,
                    },
                )
            except Exception as e:
                print(f"Warning: Could not read {file_path}: {e}", file=sys.stderr)

        elif suffix == JSON_EXT:
            try:
                raw_text = file_path.read_text(encoding="utf-8")
                content = json.loads(raw_text)
                line_count = len(raw_text.splitlines())
                decomposed_key = to_decomposed_key(rel_path)
                entry_id = content.get("id") if isinstance(content, dict) else None
                if not entry_id and decomposed_key is not None:
                    entry_id = tool_id_from_decomposed_rel(decomposed_key)
                chunk_id = entry_id or file_path.stem
                json_entries.append(
                    {
                        "id": chunk_id,
                        "name": chunk_id,
                        "file_path": rel_path,
                        "score": 0.0,
                        "start_line": 1,
                        "end_line": line_count,
                        "language": "json",
                        "content": content,
                    },
                )
            except json.JSONDecodeError as exc:
                raise json.JSONDecodeError(
                    f"Invalid JSON in {file_path}: {exc.msg}",
                    exc.doc,
                    exc.pos,
                ) from exc
            except Exception as e:
                print(f"Warning: Could not read {file_path}: {e}", file=sys.stderr)

    if not md_entries and not json_entries:
        print(f"Warning: No .json or .md files found in {dir_path}", file=sys.stderr)

    return {"md": md_entries, "json": json_entries}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override into base. Dicts are merged; other values are overwritten."""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def get_root_tool_path(file_path: Path, decomposed_dir: Path) -> Path | None:
    """Given any file under decomposed_dir, return its root tool file path."""
    key = get_root_tool_key(str(file_path))
    if key is None:
        return None
    rel = Path(key).relative_to(DECOMPOSED_ROOT)
    return decomposed_dir / rel


def climb_and_merge(leaf_path: Path | str, catalog: DecomposedCatalog | Path) -> dict[str, Any]:
    """Merge a surviving optional leaf up through ancestor property files in ``catalog``.

    Called for every optional leaf that passed ``optional_leaf_survived_rerank``. Ancestors
    present in the survivor store are merged in; missing ancestors are skipped (the leaf
    file already embeds its decomposition path).
    """
    if isinstance(catalog, Path):
        catalog = DecomposedCatalog.from_directory(catalog)

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
    catalog: DecomposedCatalog | CatalogIndex | None = None,
    decomposed_dir: Path | str | None = None,
    apply_decomposed_score_filter: bool = True,
    preserve_values: frozenset[str] | None = None,
    system_policy: str | None = None,
    mcp_policy: str | None = None,
) -> list[dict[str, Any]]:
    """
    Reconstruct merged tool schemas from search/rerank/llm output.

    Provide one of:
    - catalog: DecomposedCatalog or CatalogIndex (in-memory)
    - decomposed_dir: path to schemas/decomposed on disk
    """
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

    if catalog is None:
        if decomposed_dir is None:
            decomposed_dir = Path("src/catalog/schemas/decomposed")
        store = DecomposedCatalog.from_directory(decomposed_dir)
    elif isinstance(catalog, DecomposedCatalog):
        store = catalog
    else:
        from build_index import CatalogIndex

        if isinstance(catalog, CatalogIndex):
            store = DecomposedCatalog.from_catalog_index(catalog)
        else:
            raise TypeError("catalog must be DecomposedCatalog, CatalogIndex, or None")

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


def _group_files(
    input_files: list[str],
    decomposed_dir: Path,
) -> tuple[dict[Path, list[Path]], set[Path]]:
    """Backward-compatible wrapper around group_files using disk paths."""
    catalog = DecomposedCatalog.from_directory(decomposed_dir)
    groups, tool_files = group_files(input_files, catalog)
    return (
        {Path(k): [Path(f) for f in files] for k, files in groups.items()},
        {Path(t) for t in tool_files},
    )


def _process_groups(
    groups: dict[Path, list[Path]],
    tool_files: set[Path],
    scores: dict[str, float],
    decomposed_dir: Path,
) -> list[dict[str, Any]]:
    """Backward-compatible wrapper around process_groups using disk paths."""
    catalog = DecomposedCatalog.from_directory(decomposed_dir)
    str_groups = {str(k): [str(f) for f in files] for k, files in groups.items()}
    str_tool_files = {str(t) for t in tool_files}
    return process_groups(str_groups, str_tool_files, scores, catalog)


def _handle_input(args: argparse.Namespace) -> tuple[list[str], dict[str, float]]:
    """Determine input files and scores from command line arguments."""
    input_files: list[str] = []
    scores: dict[str, float] = {}

    if args.json_file:
        try:
            with open(args.json_file, encoding="utf-8") as f:
                data = json.load(f)
                input_files, scores = parse_json_input(data)
        except (json.JSONDecodeError, OSError) as e:
            print(f"Error reading JSON file: {e}", file=sys.stderr)
            sys.exit(1)
    elif args.json_string:
        try:
            data = json.loads(args.json_string)
            input_files, scores = parse_json_input(data)
        except json.JSONDecodeError as e:
            print(f"Error parsing JSON string: {e}", file=sys.stderr)
            sys.exit(1)
    elif args.files:
        input_files = args.files

    if not input_files:
        print("Error: No files provided or failed to extract paths from JSON.", file=sys.stderr)
        sys.exit(1)

    return input_files, scores


def main() -> None:
    parser = argparse.ArgumentParser(description="Retrieve and merge decomposed tool schemas.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--json-file",
        help="JSON file containing list of decomposed schema files to merge",
    )
    group.add_argument(
        "--json-string",
        help="JSON string containing list of decomposed schema files to merge",
    )
    group.add_argument("--files", nargs="+", help="List of decomposed schema files to merge")
    args = parser.parse_args()

    input_files, scores = _handle_input(args)

    decomposed_dir = Path("src/catalog/schemas/decomposed")
    if not decomposed_dir.exists():
        print(f"Error: {decomposed_dir} does not exist.", file=sys.stderr)
        sys.exit(1)

    catalog = DecomposedCatalog.from_directory(decomposed_dir)
    groups, tool_files = group_files(input_files, catalog)
    tools = process_groups(groups, tool_files, scores, catalog)
    print(json.dumps({"tools": tools}, indent=2))


if __name__ == "__main__":
    main()
