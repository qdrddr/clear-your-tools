"""Central CLI and shared utilities for the reverse HTTP proxy."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

from configs import (
    DEFAULT_REVERSE_PORT,
    load_config,
    proxy_http2_settings,
    require_proxy_env,
    resolve_reverse_port,
    stats_db_path,
)

logger = logging.getLogger(__name__)


def _run_stats_cli(args: argparse.Namespace, config: dict[str, Any]) -> None:
    from db import StatsDB, empty_totals, format_events, format_totals
    from pricing import compute_stats_costs, empty_costs

    db_path = stats_db_path(config)
    db = StatsDB.open_for_query(db_path)
    try:
        if args.stats_command == "totals":
            period = getattr(args, "period", "all")
            totals = db.query_totals(period) if db is not None else empty_totals()
            costs = (
                compute_stats_costs(totals, db.query_stage_model_tokens(period), config)
                if db is not None
                else empty_costs(config)
            )
            print(format_totals(totals, costs))
        elif args.stats_command == "summary":
            totals = db.query_summary(args.period) if db is not None else empty_totals()
            costs = (
                compute_stats_costs(totals, db.query_stage_model_tokens(args.period), config)
                if db is not None
                else empty_costs(config)
            )
            print(format_totals(totals, costs))
        elif args.stats_command == "events":
            events = db.query_events(args.limit) if db is not None else []
            if args.json:
                print(json.dumps(events, indent=2))
            else:
                print(format_events(events))
    finally:
        if db is not None:
            db.close()


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
    from proxy_reverse import serve_reverse_async

    await serve_reverse_async(
        config,
        host="0.0.0.0",
        port=reverse_port,
        debug=debug,
        debug_terminate=debug_dry_run,
        debug_strict=debug_strict,
        http2_upstream=http2_upstream,
        http2_serve=http2_serve,
        ssl_keyfile=ssl_keyfile,
        ssl_certfile=ssl_certfile,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Reverse HTTP proxy")
    subparsers = parser.add_subparsers(dest="command")

    serve_parser = subparsers.add_parser("serve", help="Run the reverse proxy")
    serve_parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"Reverse listen port (default from config, else {DEFAULT_REVERSE_PORT})",
    )
    serve_parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config.yaml (default: ./config.yaml, then ~/.configs/cyt/config.yaml)",
    )
    serve_parser.add_argument(
        "--debug",
        action="store_true",
        help="Log transformed requests to {endpoint}.log and forward to upstream",
    )
    serve_parser.add_argument(
        "--debug-dry-run",
        action="store_true",
        help="Dry-run: log transformed requests to {endpoint}.log without calling upstream",
    )
    serve_parser.add_argument(
        "--debug-strict",
        action="store_true",
        help="With --debug-dry-run, return 502 when pruning did not apply",
    )
    serve_parser.add_argument(
        "--http2-upstream",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    serve_parser.add_argument("--http2-serve", action=argparse.BooleanOptionalAction, default=None)
    serve_parser.add_argument("--ssl-keyfile", type=Path, default=None)
    serve_parser.add_argument("--ssl-certfile", type=Path, default=None)

    stats_parser = subparsers.add_parser("stats", help="Query persisted proxy stats")
    stats_sub = stats_parser.add_subparsers(dest="stats_command", required=True)
    stats_totals = stats_sub.add_parser("totals")
    stats_totals.add_argument("--period", choices=["day", "week", "month", "all"], default="all")
    stats_totals.add_argument("--config", type=Path, default=None)
    stats_summary = stats_sub.add_parser("summary")
    stats_summary.add_argument("--period", choices=["day", "week", "month", "all"], default="day")
    stats_summary.add_argument("--config", type=Path, default=None)
    stats_events = stats_sub.add_parser("events")
    stats_events.add_argument("--limit", type=int, default=20)
    stats_events.add_argument("--json", action="store_true")
    stats_events.add_argument("--config", type=Path, default=None)

    parser.add_argument("--port", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--config", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--debug", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--debug-dry-run", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--debug-strict", action="store_true", help=argparse.SUPPRESS)

    args = parser.parse_args()

    if args.command == "stats":
        config = load_config(getattr(args, "config", None))
        _run_stats_cli(args, config)
        return

    if args.command is None:
        args.command = "serve"
        for attr, default in (
            ("debug", False),
            ("debug_dry_run", False),
            ("debug_strict", False),
        ):
            if not hasattr(args, attr):
                setattr(args, attr, default)

    if args.debug or args.debug_dry_run:
        logging.basicConfig(
            level=logging.INFO,
            format="%(levelname)s:%(name)s: %(message)s",
            force=True,
        )

    config = load_config(args.config)
    try:
        require_proxy_env(config)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    from tool_policies import configure_policies_from_config

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
            reverse_port=resolve_reverse_port(config, args.port),
            debug=args.debug or args.debug_dry_run,
            debug_dry_run=args.debug_dry_run,
            debug_strict=args.debug_strict,
            http2_upstream=http2_upstream,
            http2_serve=http2_serve,
            ssl_keyfile=ssl_keyfile,
            ssl_certfile=ssl_certfile,
        ),
    )


if __name__ == "__main__":
    main()
