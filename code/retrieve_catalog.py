#!/usr/bin/env python3
"""Reconstruct tool schemas from decomposed property files."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any


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
    rel = file_path.relative_to(decomposed_dir)
    if len(rel.parts) < 2:
        return None
    server = rel.parts[0]
    tool_name = rel.parts[1]
    if tool_name.endswith(".json"):
        tool_name = tool_name[:-5]
    return decomposed_dir / server / f"{tool_name}.json"


def climb_and_merge(leaf_path: Path, decomposed_dir: Path) -> dict[str, Any]:
    """Load a property file and merge it up through existing parent files until the tool level."""
    with open(leaf_path) as f:
        current: dict[str, Any] = json.load(f)

    current_path = leaf_path.parent

    while True:
        parent_dir = current_path.parent

        # Stop when we would step outside the decomposed directory
        if parent_dir == decomposed_dir or not str(parent_dir).startswith(str(decomposed_dir)):
            break

        parent_file = parent_dir / (current_path.name + ".json")

        if parent_file.exists():
            with open(parent_file) as f:
                parent: dict[str, Any] = json.load(f)
            current = deep_merge(parent, current)
            current_path = parent_dir
        else:
            # Parent file doesn't exist; keep current and move up
            current_path = parent_dir

    return current


def parse_json_input(data: Any) -> tuple[list[str], dict[str, float]]:
    """Extract file paths and scores from various JSON formats."""
    input_files = []
    scores = {}

    # Handle 'md' root key for scores if it exists
    if isinstance(data, dict) and "md" in data:
        for entry in data["md"]:
            if isinstance(entry, dict) and "content" in entry and "score" in entry:
                scores[entry["content"]] = float(entry["score"])

    # The format could be a list of objects or a dict of lists of objects
    if isinstance(data, dict):
        for key, value in data.items():
            if key == "md":
                continue
            if isinstance(value, list):
                for entry in value:
                    if isinstance(entry, dict) and "file_path" in entry:
                        input_files.append(entry["file_path"])
            elif isinstance(value, dict) and "file_path" in value:
                input_files.append(value["file_path"])
    elif isinstance(data, list):
        for entry in data:
            if isinstance(entry, dict) and "file_path" in entry:
                input_files.append(entry["file_path"])

    return input_files, scores


def filter_and_sort_enums(schema: Any, scores: dict[str, float]) -> None:
    """Recursively find 'enum' arrays in schema and apply filtering/sorting by score."""
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key == "enum" and isinstance(value, list):
                # Process enum list
                items_with_scores = []
                for item in value:
                    score = scores.get(str(item), 0.0)
                    items_with_scores.append((item, score))

                # Sort by score descending
                items_with_scores.sort(key=lambda x: x[1], reverse=True)

                # Filtering logic:
                # 1. check if first 3 are >= 0.2
                # 2. if yes, remove all < 0.2
                # 3. otherwise keep first 3
                first_3_above_threshold = True
                for i in range(min(3, len(items_with_scores))):
                    if items_with_scores[i][1] < 0.2:
                        first_3_above_threshold = False
                        break

                if first_3_above_threshold:
                    result_items = [item for item, score in items_with_scores if score >= 0.2]
                else:
                    result_items = [item for item, score in items_with_scores[:3]]

                schema[key] = result_items
            else:
                filter_and_sort_enums(value, scores)
    elif isinstance(schema, list):
        for item in schema:
            filter_and_sort_enums(item, scores)


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
    group.add_argument(
        "--files",
        nargs="+",
        help="List of decomposed schema files to merge",
    )
    args = parser.parse_args()

    input_files = []
    scores = {}

    if args.json_file:
        try:
            with open(args.json_file) as f:
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

    decomposed_dir = Path("code/catalog/schemas/decomposed")
    if not decomposed_dir.exists():
        print(f"Error: {decomposed_dir} does not exist.", file=sys.stderr)
        sys.exit(1)

    # Resolve paths and categorize
    groups: dict[Path, list[Path]] = {}
    tool_files: set[Path] = set()

    for f in input_files:
        p = Path(f)
        if not p.exists():
            print(f"Warning: File not found: {p}", file=sys.stderr)
            continue

        try:
            rel = p.relative_to(decomposed_dir)
        except ValueError:
            # File is not under decomposed_dir
            continue

        parts = rel.parts
        is_tool = len(parts) == 2 and parts[1].endswith(".json")

        root_tool = get_root_tool_path(p, decomposed_dir)
        if root_tool is None:
            # Skip files directly in decomposed_dir that aren't part of a tool structure
            continue

        if is_tool:
            tool_files.add(p)

        groups.setdefault(root_tool, []).append(p)

    # Process each tool group
    for root_tool, files in groups.items():
        if not root_tool.exists():
            print(f"Warning: Root tool file not found: {root_tool}", file=sys.stderr)
            continue
        with open(root_tool) as f:
            base_tool = json.load(f)

        server_name = root_tool.parent.name
        tool_name_in_schema = base_tool.get("name", root_tool.stem)

        for f in files:
            if f in tool_files:
                continue
            climbed = climb_and_merge(f, decomposed_dir)
            base_tool = deep_merge(base_tool, climbed)

        base_tool["name"] = f"{server_name}_{tool_name_in_schema}"

        # Apply enum filtering and sorting if scores are available
        if scores:
            filter_and_sort_enums(base_tool, scores)

        print(json.dumps(base_tool, indent=2))
        print()


if __name__ == "__main__":
    main()
