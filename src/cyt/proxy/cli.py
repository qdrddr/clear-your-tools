"""Central CLI and shared utilities for the reverse HTTP proxy."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

from cyt import __version__
from cyt.config import (
    DEFAULT_REVERSE_PORT,
    DEFAULT_USER_CONFIG_PATH,
    load_config,
    load_user_config_overlay,
    proxy_http2_settings,
    remote_pruning_pipeline_configured,
    resolve_config_path,
    resolve_reverse_port,
    resolve_setup_config_path,
    stats_db_path,
)

logger = logging.getLogger(__name__)

LOCAL_SERVE_HOST = "127.0.0.1"
_STATS_SUBCOMMANDS = ("totals", "summary", "events")


def _run_stats_cli(args: argparse.Namespace, config: dict[str, Any]) -> None:
    from cyt.common.pricing import compute_stats_costs, empty_costs
    from cyt.proxy.stats import StatsDB, empty_totals, format_events, format_totals
    from cyt.proxy.stats_config_sync import sync_models_from_stats_db

    db_path = stats_db_path(config)
    for line in sync_models_from_stats_db(db_path, DEFAULT_USER_CONFIG_PATH):
        print(line, file=sys.stderr)
    config = load_config(getattr(args, "config", None))
    db = StatsDB.open_for_query(db_path)
    try:
        if args.stats_command == "totals":
            period = getattr(args, "period", "all")
            totals = db.query_totals(period) if db is not None else empty_totals()
            costs = (
                compute_stats_costs(
                    db.query_stage_model_tokens(period),
                    db.query_upstream_saved_tokens(period),
                    config,
                )
                if db is not None
                else empty_costs()
            )
            print(format_totals(totals, costs))
        elif args.stats_command == "summary":
            totals = db.query_summary(args.period) if db is not None else empty_totals()
            costs = (
                compute_stats_costs(
                    db.query_stage_model_tokens(args.period),
                    db.query_upstream_saved_tokens(args.period),
                    config,
                )
                if db is not None
                else empty_costs()
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
        choices=("anthropic", "openai"),
        default=None,
        help="Upstream protocol kind (required with --upstream)",
    )
    proxy_parser.add_argument(
        "--upstream-name",
        default=None,
        help="Upstream endpoint name (default: derived from URL second-level domain)",
    )

    stats_parser = subparsers.add_parser("stats", help="Query persisted proxy stats")
    stats_sub = stats_parser.add_subparsers(dest="stats_command", required=False)
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


def _ensure_proxy_defaults(args: argparse.Namespace) -> None:
    if args.command is not None:
        return
    args.command = "proxy"
    for attr, default in (
        ("debug", False),
        ("debug_dry_run", False),
        ("debug_strict", False),
        ("upstream", None),
        ("upstream_kind", None),
        ("upstream_name", None),
    ):
        if not hasattr(args, attr):
            setattr(args, attr, default)


def _apply_upstream_cli_args(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> str | None:
    upstream_url = getattr(args, "upstream", None)
    upstream_kind = getattr(args, "upstream_kind", None)
    upstream_name = getattr(args, "upstream_name", None)
    if (upstream_url is None) != (upstream_kind is None):
        parser.error("--upstream and --upstream-kind must be supplied together")
    if upstream_name is not None and (upstream_url is None or upstream_kind is None):
        parser.error("--upstream-name requires --upstream and --upstream-kind")
    if upstream_url is None or upstream_kind is None:
        return None

    from cyt.proxy.setup import apply_upstream_cli_to_config

    config_path = resolve_config_path(args.config)
    return apply_upstream_cli_to_config(
        config_path,
        upstream_url=upstream_url,
        upstream_kind=upstream_kind,
        upstream_name=upstream_name,
    )


_BM25_FALLBACK_MESSAGE = (
    "No pruner pipeline configured: fallback to BM25. "
    "Please run to configure more advanced pruning:\n"
    "  cyt setup"
)


def _apply_bm25_fallback_if_needed(config: dict[str, Any], config_path: Path) -> None:
    user_config = load_user_config_overlay(config_path)
    if remote_pruning_pipeline_configured(user_config):
        return
    print(_BM25_FALLBACK_MESSAGE, file=sys.stderr)
    pruning = config.setdefault("pruning", {})
    if isinstance(pruning, dict):
        pruning["pipeline"] = ["bm25"]


def _run_proxy_command(args: argparse.Namespace, *, upstream_endpoint: str | None = None) -> None:
    if args.debug or args.debug_dry_run:
        logging.basicConfig(
            level=logging.INFO,
            format="%(levelname)s:%(name)s: %(message)s",
            force=True,
        )

    config_path = resolve_config_path(args.config)
    config = load_config(args.config)
    _apply_bm25_fallback_if_needed(config, config_path)
    reverse_port = resolve_reverse_port(config, args.port)
    if upstream_endpoint is not None:
        from cyt.proxy.setup import print_proxy_urls

        print_proxy_urls(reverse_port, [upstream_endpoint])

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
            reverse_port=reverse_port,
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

    _ensure_proxy_defaults(args)
    upstream_endpoint = _apply_upstream_cli_args(parser, args)
    _run_proxy_command(args, upstream_endpoint=upstream_endpoint)


if __name__ == "__main__":
    main()
