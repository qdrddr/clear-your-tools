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


def _add_tiers_status_like_parser(
    tiers_sub: argparse._SubParsersAction,
    name: str,
    *,
    help_text: str,
) -> None:
    parser = tiers_sub.add_parser(name, help=help_text)
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    add_status_filter_arguments(parser)
    parser.set_defaults(tiers_handler=run_tiers_status)


def add_tiers_parser(subparsers: argparse._SubParsersAction) -> None:
    tiers_parser = subparsers.add_parser("tiers", help="Tier manager status and configuration")
    tiers_sub = tiers_parser.add_subparsers(dest="tiers_command", required=True)
    _add_tiers_status_like_parser(
        tiers_sub,
        "stats",
        help_text="Show tier statistics, epoch state, and per-entity details",
    )
    _add_tiers_status_like_parser(
        tiers_sub,
        "list",
        help_text="Alias for stats (same output and filters)",
    )


def _status_error(message: str, *, json_output: bool) -> int:
    if json_output:
        print(json.dumps({"error": message}, indent=2))
    else:
        print(message, file=sys.stderr)
    return 2


def _apply_skill_path_filter_to_status(
    status_payload: dict,
    *,
    path_root: Path,
    config: dict,
    project_root: Path,
    status_agent: str,
    manager: object,
    wake_cycle_id: int,
) -> None:
    from cyt.tiers.config import tier_section_config
    from cyt.tiers.status_detail import (
        filter_skill_detail_by_path,
        filter_skill_detail_by_permissions,
    )

    skill_cfg = tier_section_config(config, kind="skill")
    now_ms = int(time.time() * 1000)
    states = getattr(manager, "_states", {})
    skills_detail = status_payload.get("skills")
    if not isinstance(skills_detail, dict):
        return
    status_payload["skills"] = filter_skill_detail_by_path(
        skills_detail,
        path_root=path_root,
        config=config,
        workspace_root=project_root,
        agent=status_agent,
        states=states,
        cfg=skill_cfg,
        wake_cycle_id=wake_cycle_id,
        now_ms=now_ms,
    )
    status_payload["skills"] = filter_skill_detail_by_permissions(
        status_payload["skills"],
        agent=status_agent,
        workspace_root=project_root,
    )


def run_tiers_status(args: argparse.Namespace) -> int:
    config = load_config()
    project_root = resolve_tier_project(workspace=args.workspace)
    if project_root is None:
        return _status_error(
            "no project resolved (need a workspace with git root or workspace markers)",
            json_output=bool(args.json),
        )
    try:
        status_agent = resolve_tier_status_agent(
            config,
            workspace_root=project_root,
            explicit=getattr(args, "agent", None),
        )
    except ValueError as exc:
        return _status_error(str(exc), json_output=bool(args.json))

    from cyt.hook.workspace_config import resolve_hook_request_config

    config, _workspace = resolve_hook_request_config(
        {"workspace_root": str(project_root)},
        status_agent,
        base_config=config,
    )

    from cyt.tiers.status_detail import validate_status_path_filter
    from cyt.tiers.status_overview import build_status_overview
    from cyt.tiers.status_view import resolve_status_path_filter

    path_root, path_display, path_error = resolve_status_path_filter(
        getattr(args, "path", None),
        project_root,
    )
    if path_error:
        return _status_error(path_error, json_output=bool(args.json))

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
            return _status_error(path_scope_error, json_output=bool(args.json))

    if getattr(args, "tier", None) and filters.tier is None:
        return _status_error(
            f"invalid --tier value: {args.tier!r} (expected T0-T4)",
            json_output=bool(args.json),
        )

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
        _apply_skill_path_filter_to_status(
            status_payload,
            path_root=path_root,
            config=config,
            project_root=project_root,
            status_agent=status_agent,
            manager=manager,
            wake_cycle_id=int(status.get("wake_cycle_id") or 0),
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
    _add_tiers_status_like_parser(sub, "stats", help_text=argparse.SUPPRESS)
    _add_tiers_status_like_parser(sub, "list", help_text=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    handler = getattr(args, "tiers_handler", None)
    if handler is None:
        parser.print_help()
        return 2
    return int(handler(args))
