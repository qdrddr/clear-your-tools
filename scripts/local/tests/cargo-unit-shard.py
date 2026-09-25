#!/usr/bin/env python3
"""List cyt-indexer unit test targets for a cargo shard.

Assignment strategies:
  round-robin — sorted targets by index % shards (legacy)
  balanced    — LPT bin-packing by estimated duration (default when weights exist)
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

_UNIT_NAME_RE = re.compile(r'^name = "(unit_[^"]+)"')


def unit_test_targets(root: Path) -> list[str]:
    cargo_toml = root / "sdk/rust/cyt-indexer/Cargo.toml"
    targets: list[str] = []
    for line in cargo_toml.read_text(encoding="utf-8").splitlines():
        match = _UNIT_NAME_RE.match(line.strip())
        if match:
            targets.append(match.group(1))
    return sorted(targets)


def _weights_path(root: Path) -> Path:
    return root / "scripts/local/tests/cargo-unit-shard-weights.json"


def load_weights(root: Path) -> tuple[dict[str, float], float]:
    weights_file = _weights_path(root)
    if not weights_file.is_file():
        return {}, 1.0
    payload = json.loads(weights_file.read_text(encoding="utf-8"))
    targets = payload.get("targets") or {}
    default = float(payload.get("default_seconds", 1.0))
    return {str(key): float(value) for key, value in targets.items()}, default


def resolve_strategy(requested: str, root: Path) -> str:
    if requested != "auto":
        return requested
    if _weights_path(root).is_file():
        return "balanced"
    return "round-robin"


def round_robin_shard(targets: list[str], shard: int, shards: int) -> list[str]:
    return [name for index, name in enumerate(targets) if index % shards == shard]


def balanced_shard(
    targets: list[str],
    shard: int,
    shards: int,
    root: Path,
) -> list[str]:
    weights, default = load_weights(root)
    weighted: list[tuple[str, float]] = [
        (name, weights.get(name, default)) for name in targets
    ]
    weighted.sort(key=lambda item: (-item[1], item[0]))

    loads = [0.0] * shards
    buckets: list[list[str]] = [[] for _ in range(shards)]
    for name, weight in weighted:
        index = min(range(shards), key=lambda i: (loads[i], len(buckets[i])))
        buckets[index].append(name)
        loads[index] += weight

    return buckets[shard]


def shard_targets(
    targets: list[str],
    shard: int,
    shards: int,
    root: Path,
    strategy: str,
) -> list[str]:
    if strategy == "round-robin":
        return round_robin_shard(targets, shard, shards)
    if strategy == "balanced":
        return balanced_shard(targets, shard, shards, root)
    raise SystemExit(f"unknown strategy: {strategy}")


def audit_balance(root: Path, shards: int, strategy: str) -> dict[str, object]:
    targets = unit_test_targets(root)
    weights, default = load_weights(root)
    bucket_loads = [0.0] * shards
    bucket_counts = [0] * shards
    for shard in range(shards):
        selected = shard_targets(targets, shard, shards, root, strategy)
        bucket_counts[shard] = len(selected)
        for name in selected:
            bucket_loads[shard] += weights.get(name, default)
    positive = [load for load in bucket_loads if load > 0]
    ratio = max(positive) / min(positive) if positive else 1.0
    return {
        "strategy": strategy,
        "shards": shards,
        "targets": len(targets),
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
        default=os.environ.get("CYT_RUST_SHARD_STRATEGY", "auto"),
        help="shard assignment strategy (default: auto → balanced when weights exist)",
    )
    parser.add_argument(
        "--audit",
        action="store_true",
        help="print JSON balance report instead of target names",
    )
    args = parser.parse_args()

    strategy = resolve_strategy(args.strategy, args.root)

    if args.audit:
        if args.shard is not None or args.shards is None:
            raise SystemExit("--audit requires --shards and must not set --shard")
        print(json.dumps(audit_balance(args.root, args.shards, strategy), indent=2))
        return 0

    if args.shard is None or args.shards is None:
        raise SystemExit("--shard and --shards are required unless --audit is set")
    if args.shards < 1:
        raise SystemExit("shards must be >= 1")
    if not (0 <= args.shard < args.shards):
        raise SystemExit(
            f"shard must satisfy 0 <= shard < shards (got {args.shard}/{args.shards})",
        )

    selected = shard_targets(unit_test_targets(args.root), args.shard, args.shards, args.root, strategy)
    for name in selected:
        print(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
