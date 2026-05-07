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


def get_root_tool_path(file_path: Path, decomposed_dir: Path) -> Path:
    """Given any file under decomposed_dir, return its root tool file path."""
    rel = file_path.relative_to(decomposed_dir)
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Retrieve and merge decomposed tool schemas.")
    parser.add_argument(
        "--files",
        nargs="+",
        required=True,
        help="List of decomposed schema files to merge",
    )
    args = parser.parse_args()

    decomposed_dir = Path("code/catalog/schemas/decomposed")
    if not decomposed_dir.exists():
        print(f"Error: {decomposed_dir} does not exist.", file=sys.stderr)
        sys.exit(1)

    # Resolve paths and categorize
    groups: dict[Path, list[Path]] = {}
    tool_files: set[Path] = set()

    for f in args.files:
        p = Path(f)
        if not p.exists():
            print(f"Error: File not found: {p}", file=sys.stderr)
            sys.exit(1)

        rel = p.relative_to(decomposed_dir)
        parts = rel.parts
        is_tool = len(parts) == 2 and parts[1].endswith(".json")

        root_tool = get_root_tool_path(p, decomposed_dir)

        if is_tool:
            tool_files.add(p)

        groups.setdefault(root_tool, []).append(p)

    # Process each tool group
    for root_tool, files in groups.items():
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
        print(json.dumps(base_tool, indent=2))
        print()


if __name__ == "__main__":
    main()
