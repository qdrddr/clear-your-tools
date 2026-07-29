#!/usr/bin/env python3
"""Rank tools and server slugs by optional input_schema properties."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, TextIO


def optional_property_count(schema: Any) -> int:
    """Count schema nodes whose description contains 'Optional.'."""
    count = 0

    def walk(node: Any) -> None:
        nonlocal count
        if isinstance(node, dict):
            desc = node.get("description")
            if isinstance(desc, str) and "Optional." in desc:
                count += 1
            for value in node.values():
                if isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                if isinstance(item, (dict, list)):
                    walk(item)

    walk(schema)
    return count


def write_slug_rows(
    out: TextIO,
    slug_totals: dict[str, int],
    slug_tool_counts: dict[str, int],
    top_n: int,
) -> None:
    slug_rows = sorted(
        (
            (total, slug_tool_counts[slug], slug)
            for slug, total in slug_totals.items()
        ),
        key=lambda r: (r[0], r[1], r[2]),
        reverse=True,
    )[:top_n]
    out.write("rank\toptional_properties\ttool_count\tserver_slug\n")
    for i, (total, tool_count, slug) in enumerate(slug_rows, 1):
        out.write(f"{i}\t{total}\t{tool_count}\t{slug}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "catalog",
        nargs="?",
        default="3395723769b6c5093347299ad294eefa2e52ae728cca187e59da34c768b944a3.txt",
        help="NDJSON tools file (one JSON object per line)",
    )
    parser.add_argument("-n", "--top", type=int, default=1000)
    parser.add_argument("-o", "--output", help="Write tool TSV to this file")
    parser.add_argument(
        "--slugs-output",
        help="Write slug TSV to this file (default: stdout, or derive from --output)",
    )
    args = parser.parse_args()

    path = Path(args.catalog)
    if not path.is_file():
        print(f"catalog not found: {path}", file=sys.stderr)
        sys.exit(1)

    rows: list[tuple[int, str, str]] = []
    slug_totals: dict[str, int] = defaultdict(int)
    slug_tool_counts: dict[str, int] = defaultdict(int)

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
            n_optional = optional_property_count(schema)
            slug = tool.get("server_slug", "")
            name = tool.get("name", "")
            rows.append((n_optional, slug, name))
            slug_totals[slug] += n_optional
            slug_tool_counts[slug] += 1

    rows.sort(key=lambda r: (r[0], r[1], r[2]), reverse=True)
    top = rows[: args.top]

    slugs_output = args.slugs_output
    if slugs_output is None and args.output:
        slugs_output = args.output.replace("tools", "slugs")

    write_tools_to_stdout = args.output is None
    write_slugs_to_stdout = slugs_output is None

    tool_out = sys.stdout if write_tools_to_stdout else open(args.output, "w", encoding="utf-8")
    try:
        tool_out.write("rank\toptional_properties\tserver_slug\tname\n")
        for i, (n_optional, slug, name) in enumerate(top, 1):
            tool_out.write(f"{i}\t{n_optional}\t{slug}\t{name}\n")
    finally:
        if not write_tools_to_stdout:
            tool_out.close()

    if write_tools_to_stdout and write_slugs_to_stdout:
        sys.stdout.write("\n")

    slug_out = (
        sys.stdout
        if write_slugs_to_stdout
        else open(slugs_output, "w", encoding="utf-8")
    )
    try:
        write_slug_rows(slug_out, slug_totals, slug_tool_counts, args.top)
    finally:
        if not write_slugs_to_stdout:
            slug_out.close()


if __name__ == "__main__":
    main()
