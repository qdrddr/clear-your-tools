"""``cyt permissions`` CLI."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cyt.permissions.inventory.mcp import McpServerInventoryItem
from cyt.permissions.paths import (
    InventoryScope,
    PermissionAgentTarget,
    PermissionScope,
    inventory_scope_label,
    is_all_agents,
    normalize_agent_target,
    normalize_cli_inventory_scope,
    normalize_cli_permission_scope,
    resolve_inventory_agent,
)

if TYPE_CHECKING:
    from cyt.permissions.inventory.skills import SkillInventoryItem
    from cyt.permissions.schema import EffectivePermissions

_WRITE_SCOPES: tuple[str, ...] = ("user", "workspace")
_LIST_SCOPES: tuple[str, ...] = ("user", "workspace", "effective")


def _add_shared_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--scope",
        choices=_LIST_SCOPES,
        default="effective",
        help="Config layer to read or write (default: effective for list/show)",
    )
    parser.add_argument(
        "--agent",
        choices=("cursor", "claude", "codex", "all"),
        default="all",
        help="Agent harness or 'all' for top-level policy (default: all)",
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Override user config path (--scope user only)",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="Workspace root (default: detected from cwd)",
    )


def add_permissions_parser(subparsers: argparse._SubParsersAction) -> None:
    permissions_parser = subparsers.add_parser(
        "permissions",
        help="Manage MCP and skills allow/deny policy",
    )
    permissions_sub = permissions_parser.add_subparsers(dest="permissions_command", required=True)
    register_permissions_subcommands(permissions_sub)


def register_permissions_subcommands(permissions_sub: argparse._SubParsersAction) -> None:
    show_parser = permissions_sub.add_parser("show", help="Show effective merged policy")
    _add_shared_flags(show_parser)
    show_parser.set_defaults(permissions_handler=run_permissions_show)

    export_parser = permissions_sub.add_parser(
        "export",
        help="Export permissions to external formats",
    )
    export_parser.add_argument("--format", choices=("claude",), required=True)
    export_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write JSON file (default: stdout)",
    )
    _add_shared_flags(export_parser)
    export_parser.set_defaults(permissions_handler=run_permissions_export)

    mcp_parser = permissions_sub.add_parser("mcp", help="MCP server and tool permissions")
    mcp_sub = mcp_parser.add_subparsers(dest="permissions_mcp_command", required=True)

    servers_parser = mcp_sub.add_parser("servers", help="MCP server permissions")
    servers_sub = servers_parser.add_subparsers(
        dest="permissions_mcp_servers_command",
        required=True,
    )
    for action in ("list", "enable", "disable"):
        p = servers_sub.add_parser(action, help=f"{action} MCP servers")
        _add_shared_flags(p)
        if action != "list":
            p.add_argument("server", help="Backend MCP server name")
        p.set_defaults(permissions_handler=_mcp_servers_handler(action))

    tools_parser = mcp_sub.add_parser("tools", help="MCP tool permissions")
    tools_sub = tools_parser.add_subparsers(dest="permissions_mcp_tools_command", required=True)
    for action in ("list", "enable", "disable"):
        p = tools_sub.add_parser(action, help=f"{action} MCP tools")
        _add_shared_flags(p)
        p.add_argument("--server", help="Filter tools to one server (list only)")
        if action == "list":
            p.set_defaults(permissions_handler=run_permissions_mcp_tools_list)
        else:
            p.add_argument("server_tool", help="SERVER/TOOL")
            p.set_defaults(permissions_handler=_mcp_tools_handler(action))

    skills_parser = permissions_sub.add_parser("skills", help="Skills permissions")
    skills_sub = skills_parser.add_subparsers(dest="permissions_skills_command", required=True)
    for action in ("list", "enable", "disable"):
        p = skills_sub.add_parser(action, help=f"{action} skills")
        _add_shared_flags(p)
        if action != "list":
            p.add_argument(
                "skill_name",
                nargs="?",
                help="Skill frontmatter name (mutually exclusive with --path)",
            )
            p.add_argument(
                "--path",
                type=Path,
                default=None,
                help="Skill directory or SKILL.md path (stored as path:…; mutually exclusive with skill_name)",
            )
        p.set_defaults(permissions_handler=_skills_handler(action))


def _policy_agent(args: argparse.Namespace) -> str:
    return str(getattr(args, "agent", "all") or "all").strip().lower() or "all"


def _resolved_agent(args: argparse.Namespace) -> str:
    return resolve_inventory_agent(getattr(args, "agent", "all"))


def _agent_target(args: argparse.Namespace) -> PermissionAgentTarget:
    return normalize_agent_target(getattr(args, "agent", "all"))


def _write_scope(args: argparse.Namespace) -> PermissionScope:
    scope = str(getattr(args, "scope", "user") or "user")
    try:
        return normalize_cli_permission_scope(scope)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


def _inventory_scope(args: argparse.Namespace) -> InventoryScope:
    scope = str(getattr(args, "scope", "effective") or "effective")
    try:
        return normalize_cli_inventory_scope(scope)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


def _workspace_root(args: argparse.Namespace) -> Path | None:
    ws = getattr(args, "workspace", None)
    if ws is not None:
        return Path(ws).expanduser()
    return None


def _print_json(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _print_sections(
    title: str,
    enabled: Sequence[str],
    disabled: Sequence[str],
    *,
    json_mode: bool,
) -> None:
    if json_mode:
        _print_json({"enabled": enabled, "disabled": disabled})
        return
    print(title)
    print("Enabled:")
    if enabled:
        for item in enabled:
            print(f"  {item}")
    else:
        print("  (none)")
    print("Disabled:")
    if disabled:
        for item in disabled:
            print(f"  {item}")
    else:
        print("  (none)")


def _format_mcp_server_line(item: McpServerInventoryItem) -> str:
    prefix = {"user": "U", "workspace": "W"}.get(item.source or "", "-")
    return f"{prefix}  {item.name}"


def _format_mcp_server_inventory(
    enabled: Sequence[McpServerInventoryItem],
    disabled: Sequence[McpServerInventoryItem],
    *,
    json_mode: bool,
) -> None:
    if json_mode:
        _print_json(
            {
                "enabled": [{"name": item.name, "source": item.source} for item in enabled],
                "disabled": [{"name": item.name, "source": item.source} for item in disabled],
            },
        )
        return
    print("Enabled:")
    if enabled:
        for item in enabled:
            print(f"  {_format_mcp_server_line(item)}")
    else:
        print("  (none)")
    print("Disabled:")
    if disabled:
        for item in disabled:
            print(f"  {_format_mcp_server_line(item)}")
    else:
        print("  (none)")


def run_permissions_show(args: argparse.Namespace) -> None:
    from cyt.permissions.merge import effective_permissions

    agent = _policy_agent(args)
    effective = effective_permissions(agent=agent, workspace_root=_workspace_root(args))
    payload = {
        "agent": agent,
        "mcp": {"deny": list(effective.mcp.deny), "allow": list(effective.mcp.allow)},
        "skills": {"deny": list(effective.skills.deny), "allow": list(effective.skills.allow)},
    }
    if args.json:
        _print_json(payload)
        return
    print(f"Effective permissions (agent={agent})")
    print("MCP deny:", ", ".join(effective.mcp.deny) or "(none)")
    print("MCP allow:", ", ".join(effective.mcp.allow) or "(none)")
    print("Skills deny:", ", ".join(effective.skills.deny) or "(none)")
    print("Skills allow:", ", ".join(effective.skills.allow) or "(none)")


def run_permissions_export(args: argparse.Namespace) -> None:
    from cyt.permissions.compile_claude import compile_claude_permissions
    from cyt.permissions.merge import effective_permissions

    if args.format != "claude":
        raise SystemExit(f"Unsupported export format: {args.format}")
    agent = _policy_agent(args)
    effective = effective_permissions(agent=agent, workspace_root=_workspace_root(args))
    compiled = compile_claude_permissions(effective)
    if args.output is None:
        _print_json({"permissions": compiled})
        return
    output_path = args.output.expanduser()
    existing: dict[str, Any] = {}
    if output_path.is_file():
        try:
            parsed = json.loads(output_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            parsed = {}
        if isinstance(parsed, dict):
            existing = parsed
    existing["permissions"] = compiled
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote Claude permissions to {output_path}")


def _load_yaml_dict(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    import yaml

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def _effective_for_args(args: argparse.Namespace) -> EffectivePermissions:
    from cyt.permissions.merge import (
        effective_permissions,
        load_workspace_all_agents_config_overlay,
        load_workspace_config_overlay,
    )

    global_cfg = None
    if getattr(args, "config", None) is not None:
        global_cfg = _load_yaml_dict(Path(args.config).expanduser())
    policy_agent = _policy_agent(args)
    inventory_agent = _resolved_agent(args)
    ws = _workspace_root(args)

    if ws and is_all_agents(policy_agent):
        ws_cfg = load_workspace_all_agents_config_overlay(workspace_root=ws)
    elif ws:
        ws_cfg = load_workspace_config_overlay(inventory_agent, workspace_root=ws)
    else:
        ws_cfg = None
    return effective_permissions(
        agent=policy_agent,
        global_config=global_cfg,
        workspace_config=ws_cfg,
        workspace_root=ws,
    )


def _print_enable_mcp_server_notice(args: argparse.Namespace, server: str) -> None:
    from cyt.permissions.match import is_mcp_server_denied

    effective = _effective_for_args(args)
    if not is_mcp_server_denied(server, effective.mcp.deny):
        return
    print(
        f"Note: {server!r} is still disabled by policy in another scope.",
        file=sys.stderr,
    )


def _print_enable_mcp_tool_notice(args: argparse.Namespace, server: str, tool: str) -> None:
    from cyt.permissions.match import is_mcp_server_denied, is_mcp_tool_denied

    effective = _effective_for_args(args)
    if not is_mcp_tool_denied(server, tool, effective.mcp.deny):
        return
    if is_mcp_server_denied(server, effective.mcp.deny):
        print(
            f"Note: {server}/{tool} remains disabled because server {server!r} is denied.",
            file=sys.stderr,
        )
        return
    print(
        f"Note: {server}/{tool} is still disabled by policy in another scope.",
        file=sys.stderr,
    )


def _print_skill_name_warnings(
    enabled: Sequence[SkillInventoryItem],
    disabled: Sequence[SkillInventoryItem],
) -> None:
    for item in (*enabled, *disabled):
        if item.name_from_frontmatter:
            continue
        print(
            f"Warning: {item.path} has no frontmatter name; using directory name {item.name!r}.",
            file=sys.stderr,
        )


def _mcp_servers_handler(action: str) -> Callable[[argparse.Namespace], None]:
    def _run(args: argparse.Namespace) -> None:
        agent = _resolved_agent(args)
        ws = _workspace_root(args)
        inv_scope = _inventory_scope(args)
        if action == "list":
            from cyt.permissions.inventory.mcp import list_mcp_servers

            enabled, disabled = list_mcp_servers(
                agent=agent,
                scope=inv_scope,
                workspace_root=ws,
                policy_agent=_policy_agent(args),
            )
            if args.json:
                _format_mcp_server_inventory(enabled, disabled, json_mode=True)
                return
            print(
                f"MCP servers (agent={agent}, inventory={inventory_scope_label(inv_scope)})",
            )
            _format_mcp_server_inventory(enabled, disabled, json_mode=False)
            return

        scope = _write_scope(args)
        target = _agent_target(args)
        policy_agent = _policy_agent(args)
        from cyt.permissions.editor import disable_mcp_server, enable_mcp_server

        path = (
            enable_mcp_server(
                args.server,
                scope=scope,
                agent_target=target,
                agent=policy_agent,
                global_config_path=args.config,
                workspace_root=ws,
            )
            if action == "enable"
            else disable_mcp_server(
                args.server,
                scope=scope,
                agent_target=target,
                agent=policy_agent,
                global_config_path=args.config,
                workspace_root=ws,
            )
        )
        print(f"Updated {path}")
        if action == "enable":
            _print_enable_mcp_server_notice(args, args.server)
        print("Restart the agent or refresh cyt-mcp for MCP catalog changes to apply.")

    return _run


def run_permissions_mcp_tools_list(args: argparse.Namespace) -> None:
    from cyt.permissions.inventory.mcp import list_mcp_tools_for_server

    agent = _resolved_agent(args)
    ws = _workspace_root(args)
    inv_scope = _inventory_scope(args)
    server = str(getattr(args, "server", "") or "").strip()
    if not server:
        raise SystemExit("--server is required for cyt permissions mcp tools list")
    enabled, disabled = list_mcp_tools_for_server(
        server,
        agent=agent,
        scope=inv_scope,
        workspace_root=ws,
        policy_agent=_policy_agent(args),
    )
    fmt_enabled = [f"{item.server}/{item.tool}" for item in enabled]
    fmt_disabled = [f"{item.server}/{item.tool}" for item in disabled]
    if args.json:
        _print_json({"enabled": fmt_enabled, "disabled": fmt_disabled})
        return
    _print_sections(
        f"MCP tools for {server} (agent={agent}, inventory={inventory_scope_label(inv_scope)})",
        fmt_enabled,
        fmt_disabled,
        json_mode=False,
    )


def _mcp_tools_handler(action: str) -> Callable[[argparse.Namespace], None]:
    def _run(args: argparse.Namespace) -> None:
        if action == "list":
            run_permissions_mcp_tools_list(args)
            return
        policy_agent = _policy_agent(args)
        ws = _workspace_root(args)
        scope = _write_scope(args)
        target = _agent_target(args)
        from cyt.permissions.editor import (
            disable_mcp_tool,
            enable_mcp_tool,
            parse_server_tool_arg,
        )

        server, tool = parse_server_tool_arg(args.server_tool)
        path = (
            enable_mcp_tool(
                server,
                tool,
                scope=scope,
                agent_target=target,
                agent=policy_agent,
                global_config_path=args.config,
                workspace_root=ws,
            )
            if action == "enable"
            else disable_mcp_tool(
                server,
                tool,
                scope=scope,
                agent_target=target,
                agent=policy_agent,
                global_config_path=args.config,
                workspace_root=ws,
            )
        )
        print(f"Updated {path}")
        if action == "enable":
            _print_enable_mcp_tool_notice(args, server, tool)
        print("Restart the agent or refresh cyt-mcp for MCP catalog changes to apply.")

    return _run


def _skills_handler(action: str) -> Callable[[argparse.Namespace], None]:
    def _run(args: argparse.Namespace) -> None:
        policy_agent = _policy_agent(args)
        ws = _workspace_root(args)
        if action == "list":
            from cyt.permissions.inventory.skills import list_skills

            enabled, disabled = list_skills(agent=policy_agent, workspace_root=ws)
            if args.json:
                _print_json(
                    {
                        "enabled": [{"name": i.name, "path": i.path} for i in enabled],
                        "disabled": [{"name": i.name, "path": i.path} for i in disabled],
                    },
                )
                return
            _print_sections(
                f"Skills (agent={policy_agent})",
                [f"{item.name} ({item.path})" for item in enabled],
                [f"{item.name} ({item.path})" for item in disabled],
                json_mode=False,
            )
            _print_skill_name_warnings(enabled, disabled)
            return
        scope = _write_scope(args)
        target = _agent_target(args)
        policy_agent = _policy_agent(args)
        skill_path = getattr(args, "path", None)
        skill_name = getattr(args, "skill_name", None)
        if skill_path is None and not skill_name:
            raise SystemExit("Provide skill_name or --path")
        if skill_path is not None and skill_name:
            raise SystemExit("Use skill_name or --path, not both")
        from cyt.permissions.editor import disable_skill, enable_skill

        path = (
            enable_skill(
                str(skill_name or ""),
                scope=scope,
                agent_target=target,
                agent=policy_agent,
                global_config_path=args.config,
                workspace_root=ws,
                skill_path=skill_path,
            )
            if action == "enable"
            else disable_skill(
                str(skill_name or ""),
                scope=scope,
                agent_target=target,
                agent=policy_agent,
                global_config_path=args.config,
                workspace_root=ws,
                skill_path=skill_path,
            )
        )
        print(f"Updated {path}")

    return _run


def run_permissions(args: argparse.Namespace) -> None:
    handler = getattr(args, "permissions_handler", None)
    if handler is None:
        raise SystemExit("usage: cyt permissions {show|export|mcp|skills} ...")
    handler(args)


def build_permissions_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cyt permissions")
    permissions_sub = parser.add_subparsers(dest="permissions_command", required=True)
    register_permissions_subcommands(permissions_sub)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_permissions_parser()
    args = parser.parse_args(argv)
    run_permissions(args)


if __name__ == "__main__":
    main()
