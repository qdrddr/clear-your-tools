"""``cyt db`` CLI."""

from __future__ import annotations

import argparse
import json

from cyt.config import load_config
from cyt.db.maintenance import run_cyt_db_maintenance


def add_db_parser(subparsers: argparse._SubParsersAction) -> None:
    db_parser = subparsers.add_parser("db", help="Local CYT SQLite database utilities")
    db_sub = db_parser.add_subparsers(dest="db_command", required=True)
    maintain = db_sub.add_parser(
        "maintain",
        help="Run retention, rollup, and cleanup for tool_examples.db and tier_state.db",
    )
    maintain.add_argument(
        "--dry-run",
        action="store_true",
        help="Report rows that would be affected without writing changes",
    )
    maintain.add_argument(
        "--no-vacuum",
        action="store_true",
        help="Skip VACUUM even when deletes occurred and vacuum is enabled in config",
    )
    maintain.add_argument("--json", action="store_true", help="Emit JSON summary")
    maintain.set_defaults(db_handler=run_db_maintain)


def _format_table_result(name: str, result: object) -> list[str]:
    from cyt.db.maintenance import DbTableMaintenanceResult

    if not isinstance(result, DbTableMaintenanceResult):
        return []
    lines = [f"{name}:"]
    if result.dry_run:
        lines.append("  (dry run)")
    for table, count in sorted(result.deleted.items()):
        if count:
            lines.append(f"  deleted {table}: {count}")
    for table, count in sorted(result.updated.items()):
        if count:
            lines.append(f"  updated {table}: {count}")
    if result.vacuumed:
        lines.append("  vacuum: yes")
    if len(lines) == 1:
        lines.append("  no changes")
    return lines


def run_db_maintain(args: argparse.Namespace) -> int:
    config = load_config()
    vacuum = False if args.no_vacuum else None
    results = run_cyt_db_maintenance(config, dry_run=bool(args.dry_run), vacuum=vacuum)
    if args.json:
        print(json.dumps(results.to_dict(), indent=2))
        return 0

    lines: list[str] = []
    if results.tool_examples is not None:
        lines.extend(_format_table_result("tool_examples.db", results.tool_examples))
    if results.tier_state is not None:
        if lines:
            lines.append("")
        lines.extend(_format_table_result("tier_state.db", results.tier_state))
    if not lines:
        print("db maintain: no databases enabled in config")
        return 0
    print("\n".join(lines))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cyt db")
    sub = parser.add_subparsers(dest="db_command", required=True)
    maintain = sub.add_parser("maintain", help=argparse.SUPPRESS)
    maintain.add_argument("--dry-run", action="store_true")
    maintain.add_argument("--no-vacuum", action="store_true")
    maintain.add_argument("--json", action="store_true")
    maintain.set_defaults(db_handler=run_db_maintain)
    args = parser.parse_args(argv)
    handler = getattr(args, "db_handler", None)
    if handler is None:
        parser.print_help()
        return 2
    return int(handler(args))
