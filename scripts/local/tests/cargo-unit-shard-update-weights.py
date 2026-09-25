#!/usr/bin/env python3
"""Regenerate cargo-unit-shard-weights.json from timing logs or a cargo profile run."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

TIMING_LINE = re.compile(
    r"^\[timing\]\s+cargo-test\s+(\S+)\s+([0-9]+(?:\.[0-9]+)?)s(?:\s|$)",
)
JSONL_KEYS = ("target", "seconds")


def list_unit_targets(root: Path) -> list[str]:
    script = root / "scripts/local/tests/cargo-unit-shard.py"
    output = subprocess.check_output(
        [sys.executable, str(script), "--shard", "0", "--shards", "1", "--root", str(root)],
        text=True,
    )
    return [line.strip() for line in output.splitlines() if line.strip()]


def parse_timing_lines(text: str) -> dict[str, float]:
    target_secs: dict[str, list[float]] = defaultdict(list)
    for line in text.splitlines():
        match = TIMING_LINE.match(line.strip())
        if match:
            target_secs[match.group(1)].append(float(match.group(2)))
    return {name: max(values) for name, values in target_secs.items()}


def parse_jsonl(path: Path) -> dict[str, float]:
    target_secs: dict[str, list[float]] = defaultdict(list)
    if not path.is_file():
        return {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        target = payload.get("target")
        seconds = payload.get("seconds")
        if target is None or seconds is None:
            continue
        target_secs[str(target)].append(float(seconds))
    return {name: max(values) for name, values in target_secs.items()}


def merge_observed(existing: dict[str, float], observed: dict[str, float]) -> dict[str, float]:
    merged = dict(existing)
    for name, seconds in observed.items():
        if name in merged:
            merged[name] = max(float(merged[name]), float(seconds))
        else:
            merged[name] = float(seconds)
    return merged


def audit_ratio(root: Path, shards: int) -> float:
    script = root / "scripts/local/tests/cargo-unit-shard.py"
    output = subprocess.check_output(
        [
            sys.executable,
            str(script),
            "--audit",
            "--shards",
            str(shards),
            "--root",
            str(root),
            "--strategy",
            "balanced",
        ],
        text=True,
    )
    return float(json.loads(output)["max_min_ratio"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[3],
        help="repository root",
    )
    parser.add_argument(
        "--from-log",
        type=Path,
        action="append",
        default=[],
        help="group log or text file containing [timing] cargo-test lines",
    )
    parser.add_argument(
        "--from-timings-log",
        type=Path,
        help="rust-unit-binary-timings.jsonl from parallel runs",
    )
    parser.add_argument(
        "--run-cargo",
        action="store_true",
        help="profile each unit test target individually (slow)",
    )
    parser.add_argument(
        "--shards",
        type=int,
        default=int(__import__("os").environ.get("PREK_RUST_UNIT_SHARDS", "4")),
        help="shard count for before/after audit output",
    )
    args = parser.parse_args()

    observed: dict[str, float] = {}
    if args.from_timings_log:
        observed = merge_observed(observed, parse_jsonl(args.from_timings_log))
    for log_path in args.from_log:
        observed = merge_observed(observed, parse_timing_lines(log_path.read_text(encoding="utf-8")))

    if args.run_cargo:
        for target in list_unit_targets(args.root):
            proc = subprocess.run(
                [
                    "bash",
                    str(args.root / "scripts/local/tests/cargo-test-category.sh"),
                    "unit-one",
                    target,
                ],
                cwd=args.root,
                capture_output=True,
                text=True,
                check=False,
            )
            text = proc.stdout + "\n" + proc.stderr
            observed = merge_observed(observed, parse_timing_lines(text))

    if not observed and not args.run_cargo:
        raise SystemExit("Provide --from-log, --from-timings-log, or --run-cargo")

    weights_path = args.root / "scripts/local/tests/cargo-unit-shard-weights.json"
    existing_targets: dict[str, float] = {}
    if weights_path.is_file():
        payload = json.loads(weights_path.read_text(encoding="utf-8"))
        existing_targets = {
            str(key): float(value) for key, value in (payload.get("targets") or {}).items()
        }

    all_targets = list_unit_targets(args.root)
    merged = merge_observed(existing_targets, observed)
    measured = sorted(observed.values())
    default = measured[len(measured) // 2] if measured else float(
        json.loads(weights_path.read_text(encoding="utf-8")).get("default_seconds", 1.0)
        if weights_path.is_file()
        else 1.0,
    )
    weights = {name: round(merged.get(name, default), 3) for name in all_targets}

    before_ratio = audit_ratio(args.root, args.shards) if weights_path.is_file() else None

    out_payload = {
        "version": 1,
        "source": f"cargo-unit-shard-update-weights.py {datetime.now(timezone.utc).isoformat()}",
        "default_seconds": round(default, 3),
        "targets": weights,
    }
    weights_path.write_text(json.dumps(out_payload, indent=2) + "\n", encoding="utf-8")

    after_ratio = audit_ratio(args.root, args.shards)
    print(f"wrote {weights_path} ({len(weights)} targets, default={default:.3f}s)")
    if before_ratio is not None:
        print(f"audit max/min ratio: before={before_ratio:.2f} after={after_ratio:.2f}")
    else:
        print(f"audit max/min ratio: after={after_ratio:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
