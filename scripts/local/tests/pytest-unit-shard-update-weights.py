#!/usr/bin/env python3
"""Regenerate pytest-unit-shard-weights.json from a pytest --durations log or run."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path


def parse_durations_log(text: str) -> dict[str, float]:
    file_secs: dict[str, float] = defaultdict(float)
    pattern = re.compile(r"^(\d+\.\d+)s call\s+(.+?)::")
    for line in text.splitlines():
        match = pattern.match(line.strip())
        if match:
            file_secs[match.group(2).split("::", 1)[0]] += float(match.group(1))
    return dict(file_secs)


def list_unit_files(root: Path) -> list[str]:
    script = root / "scripts/local/tests/pytest-unit-shard.py"
    output = subprocess.check_output(
        [sys.executable, str(script), "--shard", "0", "--shards", "1", "--root", str(root), "--strategy", "round-robin"],
        text=True,
    )
    return [line.strip() for line in output.splitlines() if line.strip()]


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
        help="pytest output file containing --durations lines",
    )
    parser.add_argument(
        "--run-pytest",
        action="store_true",
        help="run unit pytest with xdist and capture durations (slow)",
    )
    args = parser.parse_args()

    if args.run_pytest:
        proc = subprocess.run(
            [
                "uv",
                "run",
                "pytest",
                "src/tests/unit",
                "--ignore=src/tests/unit/gherkin",
                "--ignore=src/tests/integration",
                "-m",
                "not integration and not paid and not gherkin and not qa and not runtime",
                "-n",
                "auto",
                "--durations=0",
                "-q",
            ],
            cwd=args.root,
            env={
                **dict(**{k: v for k, v in __import__("os").environ.items()}),
                "CYT_RUN_INTEGRATION_TESTS": "",
                "CYT_RUN_QA_TESTS": "",
                "CYT_RUN_RUNTIME_TESTS": "",
            },
            capture_output=True,
            text=True,
            check=False,
        )
        log_text = proc.stdout + "\n" + proc.stderr
    elif args.from_log:
        log_text = args.from_log.read_text(encoding="utf-8")
    else:
        raise SystemExit("Provide --from-log PATH or --run-pytest")

    file_secs = parse_durations_log(log_text)
    all_files = list_unit_files(args.root)
    measured = sorted(file_secs.values())
    default = measured[len(measured) // 2] if measured else 1.0
    weights = {path: round(file_secs.get(path, default), 3) for path in all_files}

    out = args.root / "scripts/local/tests/pytest-unit-shard-weights.json"
    out.write_text(
        json.dumps(
            {
                "version": 1,
                "source": "pytest-unit-shard-update-weights.py",
                "default_seconds": round(default, 3),
                "files": weights,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {out} ({len(weights)} files, default={default:.3f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
