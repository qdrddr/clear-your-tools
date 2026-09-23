"""``cyt tiers`` CLI."""

from __future__ import annotations

import argparse
import json
import os
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
    from cyt.hook.workspace_resolution import absolute_workspace_arg

    parser.add_argument(
        "--workspace",
        type=absolute_workspace_arg,
        default=None,
        metavar="PATH",
        help=(
            "Full absolute project directory or repo root; defaults to detected workspace "
            "from cwd. Not ./, ../, or ~."
        ),
    )
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
    projects_parser = tiers_sub.add_parser(
        "projects",
        help="List all projects with tier statistics in the shared database",
    )
    projects_parser.add_argument("--json", action="store_true")
    projects_parser.set_defaults(tiers_handler=run_tiers_projects)


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


def _maybe_warn_ambiguous_cyt_repo_resolution(
    *,
    project_root: Path,
    explicit_workspace: Path | None,
    json_output: bool,
) -> None:
    if explicit_workspace is not None or json_output:
        return
    from cyt.tiers.config import cyt_package_git_root

    cyt_root = cyt_package_git_root()
    if cyt_root is None or project_root.resolve() != cyt_root.resolve():
        return
    from cyt.hook.workspace_resolution import CYT_WORKSPACE_ENV

    if os.environ.get("CURSOR_WORKSPACE_LABEL") or os.environ.get(CYT_WORKSPACE_ENV):
        return

    from cyt.tiers.config import tier_state_db_path
    from cyt.tiers.store import TierStore

    config = load_config()
    store = TierStore.open(tier_state_db_path(config))
    try:
        projects = store.list_projects()
    finally:
        store.close()
    other_roots = [
        row["root_path"]
        for row in projects
        if Path(str(row["root_path"])).resolve() != cyt_root.resolve()
    ]
    if not other_roots:
        return
    print(
        "tiers stats: scoped to cyt source repo (uv --directory). "
        "Set .vscode/settings.json terminal.integrated.env CYT_WORKSPACE=${workspaceFolder} "
        "for your OS (run cyt hook cursor|claude|codex), pass --workspace <repo>, "
        "or use ~/.cursor/hooks/cyt/uv.ps1 / uv.sh as fallback.",
        file=sys.stderr,
    )


def _resolve_tiers_status_project(args: argparse.Namespace) -> tuple[Path | None, str | None]:
    try:
        project_root = resolve_tier_project(workspace=args.workspace)
    except Exception as exc:
        from cyt.hook.workspace_resolution import WorkspaceResolutionConflictError

        if isinstance(exc, WorkspaceResolutionConflictError):
            return None, str(exc)
        raise
    if project_root is None:
        return None, ("no project resolved (need a workspace with git root or workspace markers)")
    return project_root, None


def _validate_tiers_status_filters(
    args: argparse.Namespace,
    *,
    project_root: Path,
    config: dict,
    status_agent: str,
) -> tuple[StatusFilters | None, Path | None, str | None]:
    from cyt.tiers.status_detail import validate_status_path_filter
    from cyt.tiers.status_view import resolve_status_path_filter

    path_root, path_display, path_error = resolve_status_path_filter(
        getattr(args, "path", None),
        project_root,
    )
    if path_error:
        return None, None, path_error

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
            return None, None, path_scope_error

    if getattr(args, "tier", None) and filters.tier is None:
        return None, None, (f"invalid --tier value: {args.tier!r} (expected T0-T4, t0-t4, or 0-4)")
    return filters, path_root, None


def _build_tiers_status_payload(
    *,
    config: dict,
    project_root: Path,
    status_agent: str,
    path_root: Path | None,
) -> dict:
    from cyt.tiers.maintenance import maybe_run_tier_maintenance_on_stats_query
    from cyt.tiers.status_overview import build_status_overview

    maybe_run_tier_maintenance_on_stats_query(config)

    manager = get_tier_manager(config, workspace=project_root)
    refresh = getattr(manager, "refresh_states_from_store", None)
    if callable(refresh):
        refresh()
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
    return status_payload


def run_tiers_status(args: argparse.Namespace) -> int:
    config = load_config()
    project_root, project_error = _resolve_tiers_status_project(args)
    if project_error is not None:
        return _status_error(project_error, json_output=bool(args.json))
    assert project_root is not None

    _maybe_warn_ambiguous_cyt_repo_resolution(
        project_root=project_root,
        explicit_workspace=getattr(args, "workspace", None),
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

    from cyt.hook.workspace_config import resolve_hook_request_config, set_hook_workspace_in_config

    config, _workspace = resolve_hook_request_config(
        {"workspace_root": str(project_root)},
        status_agent,
        base_config=config,
    )
    config = set_hook_workspace_in_config(config, project_root)

    filters, path_root, filter_error = _validate_tiers_status_filters(
        args,
        project_root=project_root,
        config=config,
        status_agent=status_agent,
    )
    if filter_error is not None:
        return _status_error(filter_error, json_output=bool(args.json))
    assert filters is not None

    status_payload = _build_tiers_status_payload(
        config=config,
        project_root=project_root,
        status_agent=status_agent,
        path_root=path_root,
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


def run_tiers_projects(args: argparse.Namespace) -> int:
    from cyt.tiers.config import tier_state_db_path
    from cyt.tiers.store import TierStore

    config = load_config()
    db_path = tier_state_db_path(config)
    store = TierStore.open(db_path)
    try:
        projects = store.list_projects()
    finally:
        store.close()

    if args.json:
        print(json.dumps({"tier_state_db": db_path, "projects": projects}, indent=2))
        return 0

    if not projects:
        print(f"No tier projects in {db_path}")
        return 0

    print(f"tier_state_db: {db_path}")
    print(f"projects: {len(projects)}")
    for row in projects:
        print(
            f"  [{row['project_id']}] {row['root_path']}  last_seen_ms={row['last_seen_ms']}",
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cyt tiers")
    sub = parser.add_subparsers(dest="tiers_command", required=True)
    _add_tiers_status_like_parser(sub, "stats", help_text=argparse.SUPPRESS)
    _add_tiers_status_like_parser(sub, "list", help_text=argparse.SUPPRESS)
    projects_parser = sub.add_parser("projects", help=argparse.SUPPRESS)
    projects_parser.add_argument("--json", action="store_true")
    projects_parser.set_defaults(tiers_handler=run_tiers_projects)
    args = parser.parse_args(argv)
    handler = getattr(args, "tiers_handler", None)
    if handler is None:
        parser.print_help()
        return 2
    return int(handler(args))
