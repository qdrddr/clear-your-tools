"""``cyt tiers`` CLI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cyt.config import load_config
from cyt.tiers.config import resolve_tier_project, tier_section_config
from cyt.tiers.manager import get_tier_manager


def add_tiers_parser(subparsers: argparse._SubParsersAction) -> None:
    tiers_parser = subparsers.add_parser("tiers", help="Tier manager status and configuration")
    tiers_sub = tiers_parser.add_subparsers(dest="tiers_command", required=True)
    status_parser = tiers_sub.add_parser("status", help="Show tier histogram and epoch state")
    status_parser.add_argument("--workspace", type=Path, default=None)
    status_parser.add_argument("--json", action="store_true")
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
    manager = get_tier_manager(config, workspace=project_root)
    status = manager.status()
    tool_cfg = tier_section_config(config, kind="tool")
    skill_cfg = tier_section_config(config, kind="skill")
    payload = {
        "project_id": status.get("project_id"),
        "root_path": status.get("root_path") or str(project_root),
        "tools": {
            "enabled": tool_cfg.enabled,
            "shadow": tool_cfg.shadow,
        },
        "skills": {
            "enabled": skill_cfg.enabled,
            "shadow": skill_cfg.shadow,
        },
        **status,
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"project_id: {payload.get('project_id')}")
        print(f"root_path: {payload.get('root_path')}")
        print(f"epoch_id: {payload.get('epoch_id')}")
        print(f"tools.enabled={tool_cfg.enabled} shadow={tool_cfg.shadow}")
        print(f"skills.enabled={skill_cfg.enabled} shadow={skill_cfg.shadow}")
        histogram = payload.get("histogram") or {}
        for kind, counts in histogram.items():
            print(f"{kind}: {counts}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cyt tiers")
    sub = parser.add_subparsers(dest="tiers_command", required=True)
    status_parser = sub.add_parser("status")
    status_parser.add_argument("--workspace", type=Path, default=None)
    status_parser.add_argument("--json", action="store_true")
    status_parser.set_defaults(tiers_handler=run_tiers_status)
    args = parser.parse_args(argv)
    handler = getattr(args, "tiers_handler", None)
    if handler is None:
        parser.print_help()
        return 2
    return int(handler(args))
