"""``cyt config`` — inspect and migrate config.yaml schema revisions."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cyt.config import DEFAULT_USER_CONFIG_PATH, _load_yaml_dict, resolve_setup_config_path
from cyt.migrations.base import ConfigScope, read_schema_version
from cyt.migrations.env import current_head
from cyt.migrations.migrate import migrate_config_file, write_config_dict
from cyt.migrations.runner import migration_history, pending_revisions, upgrade_config_dict
from cyt.migrations.workspace_paths import resolve_workspace_config_path


def _resolve_scope_path(
    *,
    config_path: Path | None,
    workspace: bool,
) -> tuple[Path, ConfigScope]:
    if workspace:
        path = resolve_workspace_config_path()
        if path is None:
            raise SystemExit(
                "No workspace detected; run from a project root or pass --config PATH.",
            )
        return path, "workspace"
    path = resolve_setup_config_path(config_path or DEFAULT_USER_CONFIG_PATH)
    return path, "global"


def _cmd_current(args: argparse.Namespace) -> None:
    path, scope = _resolve_scope_path(config_path=args.config, workspace=args.workspace)
    if not path.is_file():
        print(f"{path}: (missing) baseline -> head {current_head()}")
        return
    raw = _load_yaml_dict(path)
    current = read_schema_version(raw)
    pending = pending_revisions(raw, scope=scope)
    print(f"path: {path}")
    print(f"scope: {scope}")
    print(f"schema_version: {current}")
    print(f"head: {current_head()}")
    if pending:
        print(f"pending: {', '.join(pending)}")
    else:
        print("pending: (none)")


def _cmd_history(_args: argparse.Namespace) -> None:
    head = current_head()
    print(f"head: {head}\n")
    for revision, down_revision, applies_to in migration_history():
        print(f"  {revision}  (down: {down_revision}, scope: {applies_to})")


def _cmd_migrate(args: argparse.Namespace) -> None:
    path, scope = _resolve_scope_path(config_path=args.config, workspace=args.workspace)
    target = args.target or current_head()
    if args.dry_run:
        raw = _load_yaml_dict(path) if path.is_file() else {}
        preview = upgrade_config_dict(raw, scope=scope, target=target)
        print(f"path: {path}")
        print(f"scope: {scope}")
        print(f"from: {preview.from_revision}")
        print(f"to: {preview.to_revision}")
        print(f"steps: {', '.join(preview.steps) or '(none)'}")
        print(f"changed: {preview.changed}")
        if preview.changed and args.show_diff:
            before = json.dumps(raw, sort_keys=True, indent=2, default=str)
            after = json.dumps(preview.cfg, sort_keys=True, indent=2, default=str)
            if before != after:
                print("\n--- migrated config (preview) ---")
                print(after)
        return

    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        write_config_dict(path, {})

    result = migrate_config_file(
        path,
        scope=scope,
        dry_run=False,
        target=target,
        backup=not args.no_backup,
    )
    if result is None:
        print(f"No config file at {path}")
        return
    print(f"Migrated {path}: {result.from_revision} -> {result.to_revision}")
    if result.steps:
        print(f"  steps: {', '.join(result.steps)}")
    elif not result.changed:
        print("  (already at target revision)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CYT config.yaml schema migrations")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Config path (default: ~/.config/cyt/config.yaml)",
    )
    common.add_argument(
        "--workspace",
        action="store_true",
        help="Use workspace .agents/cyt/config/config.yaml for cwd",
    )

    sub.add_parser("current", parents=[common], help="Show schema version and pending migrations")
    sub.add_parser("history", help="List migration revision chain")

    migrate_parser = sub.add_parser("migrate", parents=[common], help="Run pending migrations")
    migrate_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show planned migration without writing",
    )
    migrate_parser.add_argument(
        "--target",
        default=None,
        help=f"Target revision (default: head {current_head()})",
    )
    migrate_parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip .bak backup before writing",
    )
    migrate_parser.add_argument(
        "--show-diff",
        action="store_true",
        help="With --dry-run, print migrated YAML JSON preview",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "current":
        _cmd_current(args)
    elif args.command == "history":
        _cmd_history(args)
    elif args.command == "migrate":
        _cmd_migrate(args)
    else:
        parser.error(f"unknown command: {args.command}")


if __name__ == "__main__":
    main(sys.argv[1:])
