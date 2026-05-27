#!/usr/bin/env python3
"""Disk-backed catalog loading and CLI for decomposed tool schemas."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_SRC_ROOT = Path(__file__).resolve().parent.parent.parent
_src_root_str = str(_SRC_ROOT)
if _src_root_str not in sys.path:
    sys.path.insert(0, _src_root_str)

from build_index import tool_id_from_decomposed_rel
from retrieve_catalog import (
    DECOMPOSED_ROOT,
    JSON_EXT,
    DecomposedCatalog,
    get_root_tool_key,
    group_files,
    parse_json_input,
    process_groups,
    to_decomposed_key,
)

MD_EXT = ".md"

DEFAULT_DECOMPOSED_DIR = _SRC_ROOT / "catalog" / "schemas" / "decomposed"


@dataclass
class DiskDecomposedCatalog(DecomposedCatalog):
    """DecomposedCatalog with disk-backed reads under ``decomposed_dir``."""

    decomposed_dir: Path = field(default_factory=lambda: DEFAULT_DECOMPOSED_DIR)

    @classmethod
    def from_directory(cls, decomposed_dir: Path | str) -> DiskDecomposedCatalog:
        return cls(decomposed_dir=Path(decomposed_dir))

    def resolve_key(self, file_path: str) -> str | None:
        """Resolve an external path to an internal decomposed key, if present."""
        candidates: list[str] = []
        normalized = to_decomposed_key(file_path)
        if normalized is not None:
            candidates.append(normalized)
        candidates.append(file_path)

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
        try:
            rel = Path(key).relative_to(DECOMPOSED_ROOT)
        except ValueError:
            return False
        return (self.decomposed_dir / rel).is_file()

    def get_json(self, key: str) -> dict[str, Any] | None:
        if key in self._json_files:
            return self._json_files[key]
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


def get_root_tool_path(file_path: Path, decomposed_dir: Path) -> Path | None:
    """Given any file under decomposed_dir, return its root tool file path."""
    key = get_root_tool_key(str(file_path))
    if key is None:
        return None
    rel = Path(key).relative_to(DECOMPOSED_ROOT)
    return decomposed_dir / rel


def _group_files(
    input_files: list[str],
    decomposed_dir: Path,
) -> tuple[dict[Path, list[Path]], set[Path]]:
    """Group input files by root tool using disk-backed catalog paths."""
    catalog = DiskDecomposedCatalog.from_directory(decomposed_dir)
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
    """Merge tool groups using disk-backed catalog paths."""
    catalog = DiskDecomposedCatalog.from_directory(decomposed_dir)
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

    decomposed_dir = DEFAULT_DECOMPOSED_DIR
    if not decomposed_dir.exists():
        print(f"Error: {decomposed_dir} does not exist.", file=sys.stderr)
        sys.exit(1)

    catalog = DiskDecomposedCatalog.from_directory(decomposed_dir)
    groups, tool_files = group_files(input_files, catalog)
    tools = process_groups(groups, tool_files, scores, catalog)
    print(json.dumps({"tools": tools}, indent=2))


if __name__ == "__main__":
    main()
