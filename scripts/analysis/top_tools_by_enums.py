#!/usr/bin/env python3
"""Rank tools by enum size in input_schema (NDJSON catalog)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _enum_key(value: Any) -> str:
    """Stable key for deduplicating enum values (JSON-compatible)."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return json.dumps(value, sort_keys=True)
    return json.dumps(value, sort_keys=True, default=str)


def enum_stats(schema: Any) -> tuple[int, int, int, int]:
    """Return (max_enum_len, total_enum_values, unique_enum_values, num_enum_fields)."""
    max_len = 0
    total = 0
    count = 0
    unique: set[str] = set()

    def walk(node: Any) -> None:
        nonlocal max_len, total, count
        if isinstance(node, dict):
            enum = node.get("enum")
            if isinstance(enum, list):
                n = len(enum)
                count += 1
                total += n
                if n > max_len:
                    max_len = n
                for value in enum:
                    unique.add(_enum_key(value))
            for v in node.values():
                if isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(node, list):
            for item in node:
                if isinstance(item, (dict, list)):
                    walk(item)

    walk(schema)
    return max_len, total, len(unique), count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "catalog",
        nargs="?",
        default="3395723769b6c5093347299ad294eefa2e52ae728cca187e59da34c768b944a3.txt",
        help="NDJSON tools file (one JSON object per line)",
    )
    parser.add_argument("-n", "--top", type=int, default=1000)
    parser.add_argument(
        "--sort",
        choices=("max", "total", "unique", "fields"),
        default="max",
        help="Sort key: max single enum (max), sum of enum list lengths (total), unique values (unique), or enum field count (fields)",
    )
    parser.add_argument("-o", "--output", help="Write TSV to this file (default: stdout)")
    args = parser.parse_args()

    path = Path(args.catalog)
    if not path.is_file():
        print(f"catalog not found: {path}", file=sys.stderr)
        sys.exit(1)

    rows: list[tuple[int, int, int, int, str, str]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            tool = json.loads(line)
            schema_raw = tool.get("input_schema")
            if not schema_raw:
                continue
            schema = (
                json.loads(schema_raw)
                if isinstance(schema_raw, str)
                else schema_raw
            )
            max_len, total, n_unique, n_fields = enum_stats(schema)
            if max_len == 0 and total == 0:
                continue
            rows.append(
                (
                    max_len,
                    total,
                    n_unique,
                    n_fields,
                    tool.get("server_slug", ""),
                    tool.get("name", ""),
                ),
            )

    key_idx = {"max": 0, "total": 1, "unique": 2, "fields": 3}[args.sort]
    rows.sort(key=lambda r: (r[key_idx], r[0], r[2]), reverse=True)
    top = rows[: args.top]

    out = open(args.output, "w", encoding="utf-8") if args.output else sys.stdout
    try:
        out.write(
            "rank\tmax_enum\ttotal_enums\tunique_enums\tenum_fields\tserver_slug\tname\n",
        )
        for i, (max_len, total, n_unique, n_fields, slug, name) in enumerate(top, 1):
            out.write(
                f"{i}\t{max_len}\t{total}\t{n_unique}\t{n_fields}\t{slug}\t{name}\n",
            )
    finally:
        if args.output:
            out.close()


if __name__ == "__main__":
    main()
