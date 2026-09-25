#!/usr/bin/env python3
"""List unit test files for a pytest shard (round-robin by sorted path)."""

from __future__ import annotations

import argparse
from pathlib import Path


def unit_test_files(root: Path) -> list[Path]:
    unit_dir = root / "src" / "tests" / "unit"
    files = sorted(
        path
        for path in unit_dir.rglob("test_*.py")
        if "gherkin" not in path.parts
    )
    return files


def shard_files(files: list[Path], shard: int, shards: int) -> list[Path]:
    return [path for index, path in enumerate(files) if index % shards == shard]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shard", type=int, required=True, help="0-based shard index")
    parser.add_argument("--shards", type=int, required=True, help="total shard count")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[3],
        help="repository root",
    )
    args = parser.parse_args()
    if args.shards < 1:
        raise SystemExit("shards must be >= 1")
    if not (0 <= args.shard < args.shards):
        raise SystemExit(f"shard must satisfy 0 <= shard < shards (got {args.shard}/{args.shards})")

    selected = shard_files(unit_test_files(args.root), args.shard, args.shards)
    for path in selected:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
