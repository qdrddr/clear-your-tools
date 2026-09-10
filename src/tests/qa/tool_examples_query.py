#!/usr/bin/env -S uv run
"""Print BM25-scored tool example selections for a fixture query id.

Usage:
  uv run src/tests/qa/tool_examples_query.py 04_oauth_gitnexus
  uv run src/tests/qa/tool_examples_query.py 04_oauth_gitnexus --json
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from cyt.tool_examples.report import format_query_score_report, format_query_score_report_json
from tests.support.tool_examples_fixtures import query_entry_by_id, run_query_score_report


def _install_clock() -> None:
    state = {"ms": 1_000_000}

    def fake_time() -> float:
        current_ms = state["ms"]
        state["ms"] += 1000
        return current_ms / 1000.0

    patch("cyt.tool_examples.store.time.time", fake_time).start()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BM25 tool-examples report for a fixture query id")
    parser.add_argument(
        "query_id",
        help="Fixture query id, e.g. 04_oauth_gitnexus",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of human text",
    )
    args = parser.parse_args(argv)
    query_id = str(args.query_id)
    try:
        query_entry_by_id(query_id)
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    _install_clock()
    with tempfile.TemporaryDirectory(prefix="cyt-tool-examples-") as tmp:
        root = Path(tmp)
        workspace = root / "repo"
        workspace.mkdir()
        (workspace / ".git").mkdir()
        db_path = root / "tool_examples.db"
        report = run_query_score_report(query_id, workspace=workspace, db_path=db_path)
    if args.json:
        sys.stdout.write(format_query_score_report_json(report))
    else:
        sys.stdout.write(format_query_score_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
