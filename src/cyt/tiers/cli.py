"""``cyt tiers`` CLI."""

from __future__ import annotations

import argparse
import json
import sys
import time
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

    from cyt.hook.workspace_config import resolve_hook_request_config

    config, _workspace = resolve_hook_request_config(
        {"workspace_root": str(project_root)},
        status_agent,
        base_config=config,
    )

    from cyt.tiers.status_detail import (
        filter_skill_detail_by_path,
        validate_status_path_filter,
    )
    from cyt.tiers.status_overview import build_status_overview
    from cyt.tiers.status_view import resolve_status_path_filter

    path_root, path_display, path_error = resolve_status_path_filter(
        getattr(args, "path", None),
        project_root,
    )
    if path_error:
        if args.json:
            print(json.dumps({"error": path_error}, indent=2))
        else:
            print(path_error, file=sys.stderr)
        return 2

    filters = StatusFilters.from_args(
        args,
        path_root=path_root,
        path_display=path_display,
    )
    if path_root is not None:
        path_scope_error = validate_status_path_filter(
            path_root,
            config=config,
            workspace_root=project_root,
            agent=status_agent,
        )
        if path_scope_error:
            if args.json:
                print(json.dumps({"error": path_scope_error}, indent=2))
            else:
                print(path_scope_error, file=sys.stderr)
            return 2

    if getattr(args, "tier", None) and filters.tier is None:
        message = f"invalid --tier value: {args.tier!r} (expected T0-T4)"
        if args.json:
            print(json.dumps({"error": message}, indent=2))
        else:
            print(message, file=sys.stderr)
        return 2

    manager = get_tier_manager(config, workspace=project_root)
    status = manager.status(config, agent=status_agent)
    status_payload = {
        **status,
        "root_path": status.get("root_path") or str(project_root),
        "agent": status.get("agent") or status_agent,
    }
    status_payload["overview"] = build_status_overview(
        status_payload,
        config=config,
        workspace_root=project_root,
        agent=status_agent,
    )

    if path_root is not None and isinstance(status_payload.get("skills"), dict):
        from cyt.tiers.config import tier_section_config

        skill_cfg = tier_section_config(config, kind="skill")
        now_ms = int(time.time() * 1000)
        states = getattr(manager, "_states", {})
        status_payload["skills"] = filter_skill_detail_by_path(
            status_payload["skills"],
            path_root=path_root,
            config=config,
            workspace_root=project_root,
            agent=status_agent,
            states=states,
            cfg=skill_cfg,
            session_id=int(status.get("session_id") or 0),
            now_ms=now_ms,
        )

    payload = apply_status_view(status_payload, filters)
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
