#!/usr/bin/env python3
"""List unit test files for a pytest shard.

Assignment strategies:
  round-robin — sorted paths by index % shards (legacy)
  balanced    — LPT bin-packing by estimated duration (default when weights exist)
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def unit_test_files(root: Path) -> list[Path]:
    unit_dir = root / "src" / "tests" / "unit"
    return sorted(
        path
        for path in unit_dir.rglob("test_*.py")
        if "gherkin" not in path.parts
    )


def _repo_relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _weights_path(root: Path) -> Path:
    return root / "scripts" / "local" / "tests" / "pytest-unit-shard-weights.json"


def load_weights(root: Path) -> tuple[dict[str, float], float]:
    weights_file = _weights_path(root)
    if not weights_file.is_file():
        return {}, 1.0
    payload = json.loads(weights_file.read_text(encoding="utf-8"))
    files = payload.get("files") or {}
    default = float(payload.get("default_seconds", 1.0))
    return {str(key): float(value) for key, value in files.items()}, default


def resolve_strategy(requested: str, root: Path) -> str:
    if requested != "auto":
        return requested
    if _weights_path(root).is_file():
        return "balanced"
    return "round-robin"


def round_robin_shard(files: list[Path], shard: int, shards: int) -> list[Path]:
    return [path for index, path in enumerate(files) if index % shards == shard]


def balanced_shard(
    files: list[Path],
    shard: int,
    shards: int,
    root: Path,
) -> list[Path]:
    weights, default = load_weights(root)
    rel_weights: list[tuple[Path, float]] = []
    for path in files:
        rel = _repo_relative(path, root)
        rel_weights.append((path, weights.get(rel, default)))

    rel_weights.sort(key=lambda item: (-item[1], _repo_relative(item[0], root)))

    loads = [0.0] * shards
    buckets: list[list[Path]] = [[] for _ in range(shards)]
    for path, weight in rel_weights:
        index = min(range(shards), key=lambda i: (loads[i], len(buckets[i])))
        buckets[index].append(path)
        loads[index] += weight

    return buckets[shard]


def shard_files(
    files: list[Path],
    shard: int,
    shards: int,
    root: Path,
    strategy: str,
) -> list[Path]:
    if strategy == "round-robin":
        return round_robin_shard(files, shard, shards)
    if strategy == "balanced":
        return balanced_shard(files, shard, shards, root)
    raise SystemExit(f"unknown strategy: {strategy}")


def audit_balance(root: Path, shards: int, strategy: str) -> dict[str, object]:
    files = unit_test_files(root)
    weights, default = load_weights(root)
    bucket_loads = [0.0] * shards
    bucket_counts = [0] * shards
    for shard in range(shards):
        selected = shard_files(files, shard, shards, root, strategy)
        bucket_counts[shard] = len(selected)
        for path in selected:
            rel = _repo_relative(path, root)
            bucket_loads[shard] += weights.get(rel, default)
    positive = [load for load in bucket_loads if load > 0]
    ratio = max(positive) / min(positive) if positive else 1.0
    return {
        "strategy": strategy,
        "shards": shards,
        "files": len(files),
        "loads": bucket_loads,
        "counts": bucket_counts,
        "max_min_ratio": ratio,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shard", type=int, help="0-based shard index")
    parser.add_argument("--shards", type=int, help="total shard count")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[3],
        help="repository root",
    )
    parser.add_argument(
        "--strategy",
        choices=("auto", "round-robin", "balanced"),
        default=os.environ.get("CYT_PYTEST_SHARD_STRATEGY", "auto"),
        help="shard assignment strategy (default: auto → balanced when weights exist)",
    )
    parser.add_argument(
        "--audit",
        action="store_true",
        help="print JSON balance report instead of file paths",
    )
    args = parser.parse_args()

    strategy = resolve_strategy(args.strategy, args.root)

    if args.audit:
        if args.shard is not None or args.shards is None:
            raise SystemExit("--audit requires --shards and must not set --shard")
        import json as json_mod

        print(json_mod.dumps(audit_balance(args.root, args.shards, strategy), indent=2))
        return 0

    if args.shard is None or args.shards is None:
        raise SystemExit("--shard and --shards are required unless --audit is set")
    if args.shards < 1:
        raise SystemExit("shards must be >= 1")
    if not (0 <= args.shard < args.shards):
        raise SystemExit(
            f"shard must satisfy 0 <= shard < shards (got {args.shard}/{args.shards})",
        )

    selected = shard_files(unit_test_files(args.root), args.shard, args.shards, args.root, strategy)
    for path in selected:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
