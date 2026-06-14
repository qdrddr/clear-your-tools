"""Central CLI and shared utilities for the reverse HTTP proxy."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sqlite3
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cyt import __version__
from cyt.config import (
    DEFAULT_REVERSE_PORT,
    load_config,
    load_user_config_overlay,
    proxy_http2_settings,
    resolve_setup_config_path,
    stats_backup_before_rollup,
    stats_db_path,
    stats_rollup_on_query,
)
from cyt.proxy.setup import normalize_upstream_kind

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from cyt.common.pricing import StatsCosts
    from cyt.proxy.stats import StatsDB

LOCAL_SERVE_HOST = "127.0.0.1"
_STATS_SUBCOMMANDS = ("totals", "summary", "events")


def _add_stats_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--add-costs",
        action="store_true",
        help="After showing stats, run a wizard to add missing LLM/reranker model costs",
    )
    parser.add_argument(
        "--no-rollup",
        action="store_true",
        help="Skip historical stats compaction for this invocation",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config.yaml (default: ./config.yaml, then ~/.config/cyt/config.yaml)",
    )


def _maybe_rollup_stats_db(
    db: StatsDB,
    db_path: str,
    config: dict[str, Any],
    *,
    no_rollup: bool,
) -> None:
    rollup_enabled = stats_rollup_on_query(config) and not no_rollup
    if not rollup_enabled:
        return

    today_backup = db.find_today_backup(db_path)
    if today_backup is not None:
        print(
            f"stats: rollup skipped (backup already exists for today: {today_backup})",
            file=sys.stderr,
        )
        return

    if stats_backup_before_rollup(config):
        backup_path = db.backup_database(db_path)
        print(f"stats: backed up to {backup_path}", file=sys.stderr)

    rollup_done = False
    try:
        result = db.rollup_historical()
        rollup_done = True
        if result.groups_merged:
            print(
                f"stats: compacted {result.rows_removed} historical rows "
                f"into {result.groups_merged} daily rollups",
                file=sys.stderr,
            )
    except sqlite3.OperationalError as exc:
        print(f"stats: rollup skipped ({exc})", file=sys.stderr)

    if not rollup_done:
        return

    try:
        db.vacuum()
    except sqlite3.OperationalError as exc:
        print(f"stats: vacuum skipped ({exc})", file=sys.stderr)


def _compute_stats_costs_for_period(
    db: StatsDB | None,
    config: dict[str, Any],
    period: str,
) -> StatsCosts:
    from cyt.common.pricing import compute_stats_costs, empty_costs

    if db is None:
        return empty_costs()
    return compute_stats_costs(
        db.query_stage_model_tokens(period),
        db.query_upstream_saved_tokens(period),
        config,
        skills_injection_tokens=db.query_skills_injection_tokens(period),
    )


def _print_stats_results(
    db: StatsDB | None,
    args: argparse.Namespace,
    config: dict[str, Any],
) -> None:
    from cyt.proxy.stats import empty_totals, format_events, format_totals

    if args.stats_command == "totals":
        period = getattr(args, "period", "all")
        totals = db.query_totals(period) if db is not None else empty_totals()
        costs = _compute_stats_costs_for_period(db, config, period)
        print(format_totals(totals, costs))
        return

    if args.stats_command == "summary":
        totals = db.query_summary(args.period) if db is not None else empty_totals()
        costs = _compute_stats_costs_for_period(db, config, args.period)
        print(format_totals(totals, costs))
        return

    if args.stats_command == "events":
        events = db.query_events(args.limit) if db is not None else []
        if args.json:
            print(json.dumps(events, indent=2))
        else:
            print(format_events(events))


def _maybe_run_add_costs_wizard(args: argparse.Namespace, user_config_path: Path) -> None:
    from cyt.proxy.setup import (
        STATS_ADD_COSTS_HINT,
        has_models_missing_costs,
        run_add_costs_wizard,
    )

    if getattr(args, "add_costs", False):
        run_add_costs_wizard(user_config_path)
        return

    if has_models_missing_costs(load_user_config_overlay(user_config_path)):
        print(STATS_ADD_COSTS_HINT, file=sys.stderr)


def _run_stats_cli(args: argparse.Namespace, config: dict[str, Any]) -> None:
    from cyt.proxy.stats import StatsDB, expand_db_path
    from cyt.proxy.stats_config_sync import sync_models_from_stats_db

    user_config_path = resolve_setup_config_path(getattr(args, "config", None))
    db_path = stats_db_path(config)
    for line in sync_models_from_stats_db(db_path, user_config_path):
        print(line, file=sys.stderr)
    config = load_config(getattr(args, "config", None))

    db: StatsDB | None = None
    expanded_db_path = expand_db_path(db_path)
    if Path(expanded_db_path).exists():
        db = StatsDB.open(db_path)
        _maybe_rollup_stats_db(
            db,
            db_path,
            config,
            no_rollup=getattr(args, "no_rollup", False),
        )

    for removed_backup in StatsDB.prune_old_backups(db_path):
        print(f"stats: removed old backup {removed_backup}", file=sys.stderr)

    try:
        _print_stats_results(db, args, config)
    finally:
        if db is not None:
            db.close()

    _maybe_run_add_costs_wizard(args, user_config_path)


def _print_stats_options() -> None:
    print(f"options: {', '.join(_STATS_SUBCOMMANDS)}")


def _ensure_stats_defaults(args: argparse.Namespace) -> None:
    if args.stats_command is not None:
        return
    _print_stats_options()
    args.stats_command = "totals"
    if not hasattr(args, "period"):
        args.period = "all"


async def run_reverse_server(
    *,
    config: dict[str, Any],
    reverse_port: int,
    debug: bool,
    debug_dry_run: bool,
    debug_strict: bool,
    http2_upstream: bool,
    http2_serve: bool,
    ssl_keyfile: str | None,
    ssl_certfile: str | None,
) -> None:
    from cyt.proxy.reverse import serve_reverse_async

    await serve_reverse_async(
        config,
        host=LOCAL_SERVE_HOST,
        port=reverse_port,
        debug=debug,
        debug_terminate=debug_dry_run,
        debug_strict=debug_strict,
        http2_upstream=http2_upstream,
        http2_serve=http2_serve,
        ssl_keyfile=ssl_keyfile,
        ssl_certfile=ssl_certfile,
    )


def _top_level_subcommands(parser: argparse.ArgumentParser) -> list[str]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return list(action.choices)
    return []


def _require_top_level_command(parser: argparse.ArgumentParser) -> None:
    names = ", ".join(_top_level_subcommands(parser))
    print(
        f"{parser.prog}: error: the following arguments are required: {names}",
        file=sys.stderr,
    )
    parser.print_help(file=sys.stderr)
    raise SystemExit(2)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reverse HTTP proxy")
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command")

    proxy_parser = subparsers.add_parser("proxy", help="Run the reverse proxy")
    proxy_parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"Reverse listen port (default from config, else {DEFAULT_REVERSE_PORT})",
    )
    proxy_parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config.yaml (default: ./config.yaml, then ~/.config/cyt/config.yaml)",
    )
    proxy_parser.add_argument(
        "--debug",
        action="store_true",
        help="Log transformed requests to {endpoint}.log and forward to upstream",
    )
    proxy_parser.add_argument(
        "--debug-dry-run",
        action="store_true",
        help="Dry-run: log transformed requests to {endpoint}.log without calling upstream",
    )
    proxy_parser.add_argument(
        "--debug-strict",
        action="store_true",
        help="With --debug-dry-run, return 502 when pruning did not apply",
    )
    proxy_parser.add_argument(
        "--http2-upstream",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    proxy_parser.add_argument("--http2-serve", action=argparse.BooleanOptionalAction, default=None)
    proxy_parser.add_argument("--ssl-keyfile", type=Path, default=None)
    proxy_parser.add_argument("--ssl-certfile", type=Path, default=None)
    proxy_parser.add_argument(
        "--upstream",
        metavar="URL",
        default=None,
        help="Upstream API base URL (writes minimal upstream config to config.yaml)",
    )
    proxy_parser.add_argument(
        "--upstream-kind",
        type=normalize_upstream_kind,
        default=None,
        help=(
            "Upstream protocol kind (optional for canonical URLs): "
            "anthropic, openai, or aliases claude/claude-code, codex"
        ),
    )
    proxy_parser.add_argument(
        "--upstream-name",
        default=None,
        help="Upstream endpoint name (default: derived from URL second-level domain)",
    )
    proxy_parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress runtime env summary before server start",
    )

    from cyt.launch.cli import add_launch_parser

    add_launch_parser(subparsers)

    stats_common = argparse.ArgumentParser(add_help=False)
    _add_stats_common_args(stats_common)
    stats_parser = subparsers.add_parser(
        "stats",
        help="Query persisted proxy stats",
        parents=[stats_common],
    )
    stats_sub = stats_parser.add_subparsers(dest="stats_command", required=False)
    stats_totals = stats_sub.add_parser("totals", parents=[stats_common])
    stats_totals.add_argument("--period", choices=["day", "week", "month", "all"], default="all")
    stats_summary = stats_sub.add_parser("summary", parents=[stats_common])
    stats_summary.add_argument("--period", choices=["day", "week", "month", "all"], default="day")
    stats_events = stats_sub.add_parser("events", parents=[stats_common])
    stats_events.add_argument("--limit", type=int, default=20)
    stats_events.add_argument("--json", action="store_true")

    skills_parser = subparsers.add_parser(
        "skills",
        help="Agent hook: session tracking and skill injection",
    )
    skills_parser.add_argument(
        "--debug",
        action="store_true",
        help="Log hook stdin and handling outcome to .debug/skills/",
    )
    skills_parser.add_argument(
        "--prompt",
        metavar="TEXT",
        default=None,
        help="Run skill search/injection for TEXT (terminal mode; skips stdin)",
    )
    skills_parser.add_argument(
        "--model",
        metavar="MODEL",
        default=None,
        help="Model name for stats when using --prompt (optional)",
    )
    skills_parser.add_argument(
        "--test",
        action="store_true",
        help="Print skills/pruning pipelines and required API key resolution (no hook I/O)",
    )

    subparsers.add_parser(
        "setup",
        help="Interactive wizard for ~/.config/cyt/config.yaml",
    ).add_argument(
        "--config",
        type=Path,
        default=None,
        help="Config path (default: ~/.config/cyt/config.yaml)",
    )

    parser.add_argument("--port", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--config", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--debug", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--debug-dry-run", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--debug-strict", action="store_true", help=argparse.SUPPRESS)
    return parser


def _uses_legacy_proxy_flags(args: argparse.Namespace) -> bool:
    return any(
        (
            args.port is not None,
            args.config is not None,
            args.debug,
            args.debug_dry_run,
            args.debug_strict,
        ),
    )


def _ensure_proxy_defaults(args: argparse.Namespace) -> None:
    args.command = "proxy"
    for attr, default in (
        ("debug", False),
        ("debug_dry_run", False),
        ("debug_strict", False),
        ("upstream", None),
        ("upstream_kind", None),
        ("upstream_name", None),
        ("http2_upstream", None),
        ("http2_serve", None),
        ("ssl_keyfile", None),
        ("ssl_certfile", None),
        ("quiet", False),
    ):
        if not hasattr(args, attr):
            setattr(args, attr, default)


def _run_proxy_command(args: argparse.Namespace) -> None:
    if getattr(args, "upstream_kind", None) is not None and getattr(args, "upstream", None) is None:
        raise SystemExit("--upstream-kind requires --upstream")
    if getattr(args, "upstream_name", None) is not None and getattr(args, "upstream", None) is None:
        raise SystemExit("--upstream-name requires --upstream")

    if args.debug or args.debug_dry_run:
        logging.basicConfig(
            level=logging.INFO,
            format="%(levelname)s:%(name)s: %(message)s",
            force=True,
        )

    from cyt.launch.env_report import print_runtime_env_report
    from cyt.proxy.bootstrap import prepare_runtime

    runtime = prepare_runtime(
        agent=None,
        config_path=args.config,
        port=args.port,
        upstream_url=getattr(args, "upstream", None),
        upstream_kind=getattr(args, "upstream_kind", None),
        upstream_name=getattr(args, "upstream_name", None),
    )
    config = runtime.config

    print_runtime_env_report(
        quiet=bool(getattr(args, "quiet", False)),
        credential_sources=runtime.credential_sources,
        port=runtime.port,
        endpoint=runtime.upstream_endpoint,
        upstream_url=runtime.upstream_url,
        include_agent_recipe=False,
    )

    if runtime.upstream_endpoint is not None:
        from cyt.proxy.setup import print_proxy_urls

        print_proxy_urls(runtime.port, [runtime.upstream_endpoint])

    from cyt.pruners.policies import configure_policies_from_config

    configure_policies_from_config(config)

    http2_settings = proxy_http2_settings(config)
    http2_upstream = (
        args.http2_upstream if args.http2_upstream is not None else http2_settings["http2_upstream"]
    )
    http2_serve = (
        args.http2_serve if args.http2_serve is not None else http2_settings["http2_serve"]
    )
    ssl_keyfile = (
        str(args.ssl_keyfile) if args.ssl_keyfile is not None else http2_settings["ssl_keyfile"]
    )
    ssl_certfile = (
        str(args.ssl_certfile) if args.ssl_certfile is not None else http2_settings["ssl_certfile"]
    )

    asyncio.run(
        run_reverse_server(
            config=config,
            reverse_port=runtime.port,
            debug=args.debug or args.debug_dry_run,
            debug_dry_run=args.debug_dry_run,
            debug_strict=args.debug_strict,
            http2_upstream=http2_upstream,
            http2_serve=http2_serve,
            ssl_keyfile=ssl_keyfile,
            ssl_certfile=ssl_certfile,
        ),
    )


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "stats":
        _ensure_stats_defaults(args)
        config = load_config(getattr(args, "config", None))
        _run_stats_cli(args, config)
        return

    if args.command == "setup":
        from cyt.proxy.setup import run_setup

        run_setup(resolve_setup_config_path(getattr(args, "config", None)))
        return

    if args.command == "skills":
        from cyt.skills.cli import run as run_skills

        run_skills(
            debug=bool(getattr(args, "debug", False)),
            prompt=getattr(args, "prompt", None),
            model=getattr(args, "model", None),
            test=bool(getattr(args, "test", False)),
        )
        return

    if args.command == "launch":
        from cyt.launch.cli import run as run_launch

        run_launch(args)
        return

    if args.command is None:
        if _uses_legacy_proxy_flags(args):
            _ensure_proxy_defaults(args)
        else:
            _require_top_level_command(parser)
    _run_proxy_command(args)


if __name__ == "__main__":
    main()
