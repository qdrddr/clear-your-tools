"""``cyt tiers`` CLI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cyt.config import load_config
from cyt.tiers.config import resolve_tier_project, resolve_tier_status_agent
from cyt.tiers.manager import get_tier_manager
from cyt.tiers.status_view import (
    StatusFilters,
    add_status_filter_arguments,
    apply_status_view,
    format_status_text,
)


def add_tiers_parser(subparsers: argparse._SubParsersAction) -> None:
    tiers_parser = subparsers.add_parser("tiers", help="Tier manager status and configuration")
    tiers_sub = tiers_parser.add_subparsers(dest="tiers_command", required=True)
    status_parser = tiers_sub.add_parser(
        "status",
        help="Show tier histogram, epoch state, and per-entity details",
    )
    status_parser.add_argument("--workspace", type=Path, default=None)
    status_parser.add_argument("--json", action="store_true")
    add_status_filter_arguments(status_parser)
    status_parser.set_defaults(tiers_handler=run_tiers_status)


def run_tiers_status(args: argparse.Namespace) -> int:
    config = load_config()
    project_root = resolve_tier_project(workspace=args.workspace)
    if project_root is None:
        message = "no project resolved (need a workspace with git root or workspace markers)"
        if args.json:
            print(json.dumps({"error": message}, indent=2))
        else:
            print(message, file=sys.stderr)
        return 2
    try:
        status_agent = resolve_tier_status_agent(
            config,
            workspace_root=project_root,
            explicit=getattr(args, "agent", None),
        )
    except ValueError as exc:
        message = str(exc)
        if args.json:
            print(json.dumps({"error": message}, indent=2))
        else:
            print(message, file=sys.stderr)
        return 2

    filters = StatusFilters.from_args(args)
    if getattr(args, "tier", None) and filters.tier is None:
        message = f"invalid --tier value: {args.tier!r} (expected T0-T4)"
        if args.json:
            print(json.dumps({"error": message}, indent=2))
        else:
            print(message, file=sys.stderr)
        return 2

    manager = get_tier_manager(config, workspace=project_root)
    status = manager.status(config, agent=status_agent)
    payload = apply_status_view(
        {
            **status,
            "root_path": status.get("root_path") or str(project_root),
            "agent": status.get("agent") or status_agent,
        },
        filters,
    )
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            format_status_text(
                payload,
                filters=filters,
                verbose=bool(getattr(args, "verbose", False)),
            ),
            end="",
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cyt tiers")
    sub = parser.add_subparsers(dest="tiers_command", required=True)
    status_parser = sub.add_parser("status")
    status_parser.add_argument("--workspace", type=Path, default=None)
    status_parser.add_argument("--json", action="store_true")
    add_status_filter_arguments(status_parser)
    status_parser.set_defaults(tiers_handler=run_tiers_status)
    args = parser.parse_args(argv)
    handler = getattr(args, "tiers_handler", None)
    if handler is None:
        parser.print_help()
        return 2
    return int(handler(args))
