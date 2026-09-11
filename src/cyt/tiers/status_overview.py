"""Infrastructure overview payload and text formatting for ``tiers status``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cyt.tiers.entity_origin import resolve_mcp_server_origin, resolve_skill_scope


def _short_path(path: Path | str) -> str:
    """Shorten *path*, using ``~`` when it lives under the user home directory."""
    expanded = Path(path).expanduser()
    home = Path.home()

    expanded_text = str(expanded)
    home_text = str(home)
    if expanded_text == home_text:
        return "~"
    home_prefix = home_text.rstrip("/") + "/"
    if expanded_text.startswith(home_prefix):
        suffix = expanded_text[len(home_prefix) :]
        return f"~/{suffix}" if suffix else "~"

    try:
        resolved = expanded.resolve()
    except OSError:
        resolved = expanded
    try:
        home_resolved = home.resolve()
        rel = resolved.relative_to(home_resolved)
        return f"~/{rel}" if str(rel) != "." else "~"
    except (OSError, ValueError):
        return expanded_text


def _path_display(
    path: Path | str,
    *,
    scope: str,
    workspace_root: Path | None,
) -> str:
    """Format a path for overview tables.

    Workspace-scoped paths are shown relative to *workspace_root*; user-scoped
    paths use ``~`` shortening.
    """
    try:
        resolved = Path(path).expanduser().resolve()
    except OSError:
        resolved = Path(path).expanduser()

    if scope == "workspace" and workspace_root is not None:
        try:
            ws = workspace_root.expanduser().resolve()
            rel = resolved.relative_to(ws)
            return "." if str(rel) == "." else str(rel)
        except (OSError, ValueError):
            pass
    return _short_path(resolved)


def _scoped_source_path_display(
    source_path: str,
    *,
    scope: str | None,
    workspace_root: Path | None,
    source_line: int | None = None,
) -> str:
    display = _path_display(
        source_path,
        scope=scope or "user",
        workspace_root=workspace_root,
    )
    if isinstance(source_line, int) and source_line > 0:
        return f"{display}:L{source_line}"
    return display


def _skill_count_in_directory(directory: Path) -> int:
    from cyt.skills.catalog import _walk_skill_md_files

    if not directory.is_dir():
        return 0
    return len(_walk_skill_md_files([str(directory)]))


def _list_mcp_config_files(*, agent: str, workspace_root: Path | None) -> list[dict[str, Any]]:
    from cyt.hook.install_scope import CytInstallScope
    from cyt.tiers.entity_origin import _mcp_config_candidates, _server_names_in_mcp_config
    from cyt_mcp.config import GLOBAL_MCP_CONFIG_PATH

    install = CytInstallScope(workspace_root=workspace_root)
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()

    def append_entry(scope: str, path: Path, *, is_aggregator: bool = False) -> None:
        try:
            resolved = path.expanduser().resolve()
        except OSError:
            resolved = path.expanduser()
        key = str(resolved)
        if key in seen:
            return
        seen.add(key)
        server_count = 0 if is_aggregator else len(_server_names_in_mcp_config(resolved))
        entries.append(
            {
                "scope": scope,
                "path": key,
                "path_display": _path_display(
                    resolved,
                    scope=scope,
                    workspace_root=workspace_root,
                ),
                "exists": resolved.is_file(),
                "server_count": server_count,
                "kind": "aggregator" if is_aggregator else "server_defs",
            },
        )

    if install.has_workspace:
        ws_agg = install.workspace_all_agents_cyt_mcp_config_path()
        if ws_agg is not None:
            append_entry("workspace", ws_agg, is_aggregator=True)
        ws_agent_mcp = install.workspace_agent_mcp_path(agent)
        if ws_agent_mcp is not None:
            append_entry("workspace", ws_agent_mcp)

    append_entry("user", GLOBAL_MCP_CONFIG_PATH.expanduser(), is_aggregator=True)
    append_entry("user", install.user_agent_mcp_path(agent))

    for scope, path in _mcp_config_candidates(agent=agent, workspace_root=workspace_root):
        append_entry(scope, path)

    return entries


def _list_mcp_servers(
    catalog_tools: list[dict[str, Any]],
    *,
    agent: str,
    workspace_root: Path | None,
) -> list[dict[str, Any]]:
    from cyt.cyt_mcp.catalog import group_tools_by_mcp_server

    grouped = group_tools_by_mcp_server(catalog_tools)
    servers: list[dict[str, Any]] = []
    for name in sorted(grouped, key=str.lower):
        tools = grouped[name]
        scope, source_path, source_line = resolve_mcp_server_origin(
            name,
            agent=agent,
            workspace_root=workspace_root,
        )
        entry: dict[str, Any] = {
            "name": name,
            "tool_count": len(tools),
        }
        if scope:
            entry["scope"] = scope
        if source_path:
            entry["source_path"] = source_path
            entry["source_path_display"] = _scoped_source_path_display(
                source_path,
                scope=scope,
                workspace_root=workspace_root,
                source_line=source_line,
            )
        servers.append(entry)
    return servers


def _list_skill_directories(
    config: dict[str, Any],
    *,
    agent: str,
    workspace_root: Path | None,
) -> list[dict[str, Any]]:
    from cyt.skills.directories import resolve_skill_directories

    directories: list[dict[str, Any]] = []
    seen: set[str] = set()
    for directory in resolve_skill_directories(config, agent=agent, workspace_root=workspace_root):
        key = str(directory)
        if key in seen:
            continue
        seen.add(key)
        scope = resolve_skill_scope(key, workspace_root=workspace_root) or "user"
        directories.append(
            {
                "scope": scope,
                "path": key,
                "path_display": _path_display(
                    directory,
                    scope=scope,
                    workspace_root=workspace_root,
                ),
                "exists": directory.is_dir(),
                "skill_count": _skill_count_in_directory(directory),
            },
        )
    return directories


def build_status_overview(
    status: dict[str, Any],
    *,
    config: dict[str, Any],
    workspace_root: Path | None,
    agent: str,
) -> dict[str, Any]:
    from cyt.hook.workspace_config import set_hook_workspace_in_config
    from cyt.tiers.config import tier_state_db_path
    from cyt.tools.master_catalog import get_master_tool_catalog, master_catalog_health_snapshot

    scoped = set_hook_workspace_in_config(config, workspace_root) if workspace_root else config
    catalog_tools = get_master_tool_catalog(scoped, blocking=True) or []
    catalog_health = master_catalog_health_snapshot(scoped)

    tools_block = status.get("tools") if isinstance(status.get("tools"), dict) else {}
    skills_block = status.get("skills") if isinstance(status.get("skills"), dict) else {}

    db_tool_count = sum(
        int(v)
        for v in (tools_block.get("histogram") or {}).values()
        if isinstance(v, int)
    )
    db_skill_count = sum(
        int(v)
        for v in (skills_block.get("histogram") or {}).values()
        if isinstance(v, int)
    )

    return {
        "epoch": {
            "epoch_id": status.get("epoch_id"),
            "session_id": status.get("session_id"),
            "epoch_start_ms": status.get("epoch_start_ms"),
            "last_request_ms": status.get("last_request_ms"),
        },
        "tiers": {
            "tools": {
                "enabled": tools_block.get("enabled"),
                "shadow": tools_block.get("shadow"),
            },
            "skills": {
                "enabled": skills_block.get("enabled"),
                "shadow": skills_block.get("shadow"),
            },
        },
        "mcp_servers": _list_mcp_servers(
            catalog_tools,
            agent=agent,
            workspace_root=workspace_root,
        ),
        "mcp_config_files": _list_mcp_config_files(agent=agent, workspace_root=workspace_root),
        "skill_directories": _list_skill_directories(
            scoped,
            agent=agent,
            workspace_root=workspace_root,
        ),
        "troubleshooting": {
            "configured_sources": catalog_health.get("configured_sources"),
            "catalog_tool_count": catalog_health.get("catalog_tool_count"),
            "tracked_catalog_tool_count": tools_block.get("tracked_catalog_tool_count"),
            "tier_state_db": _short_path(tier_state_db_path(scoped)),
            "db_tool_entities": db_tool_count,
            "db_skill_entities": db_skill_count,
            **{
                key: catalog_health[key]
                for key in ("catalog_age_seconds", "composite_fingerprint_prefix", "source_fingerprints")
                if key in catalog_health
            },
        },
    }


def _format_table_row(columns: list[str], widths: list[int]) -> str:
    parts: list[str] = []
    for column, width in zip(columns, widths, strict=True):
        text = column if len(column) <= width else column[: max(0, width - 3)] + "..."
        parts.append(text.ljust(width))
    return "  ".join(parts)


def format_overview_text(payload: dict[str, Any]) -> str:
    from cyt.tiers.status_view import format_project_header

    lines = format_project_header(payload).splitlines()
    overview = payload.get("overview")
    if not isinstance(overview, dict):
        return "\n".join(lines).rstrip() + "\n"

    epoch = overview.get("epoch")
    if isinstance(epoch, dict):
        lines.append(
            f"epoch_id: {epoch.get('epoch_id')}  session_id: {epoch.get('session_id')}",
        )
        if epoch.get("last_request_ms"):
            lines.append(
                f"epoch_start_ms: {epoch.get('epoch_start_ms')}  "
                f"last_request_ms: {epoch.get('last_request_ms')}",
            )

    tiers = overview.get("tiers")
    if isinstance(tiers, dict):
        tools_tiers = tiers.get("tools")
        skills_tiers = tiers.get("skills")
        if isinstance(tools_tiers, dict):
            lines.append(
                "tools: "
                f"enabled={tools_tiers.get('enabled')} "
                f"shadow={tools_tiers.get('shadow')}",
            )
        if isinstance(skills_tiers, dict):
            lines.append(
                "skills: "
                f"enabled={skills_tiers.get('enabled')} "
                f"shadow={skills_tiers.get('shadow')}",
            )

    lines.append("")
    lines.append("=== mcp servers ===")
    servers = overview.get("mcp_servers")
    if isinstance(servers, list) and servers:
        widths = [11, 22, 7, 44]
        lines.append(_format_table_row(["Scope", "Server", "Total", "Path"], widths))
        for row in servers:
            if not isinstance(row, dict):
                continue
            scope = str(row.get("scope") or "")
            scope_label = f"[{scope}]" if scope else ""
            path_display = str(row.get("source_path_display") or row.get("source_path") or "")
            lines.append(
                _format_table_row(
                    [
                        scope_label,
                        str(row.get("name") or ""),
                        str(row.get("tool_count") or 0),
                        path_display,
                    ],
                    widths,
                ),
            )
    else:
        lines.append("  (none)")

    lines.append("")
    lines.append("=== mcp config files ===")
    config_files = overview.get("mcp_config_files")
    if isinstance(config_files, list) and config_files:
        widths = [11, 7, 48]
        lines.append(_format_table_row(["Scope", "Total", "Path"], widths))
        for row in config_files:
            if not isinstance(row, dict):
                continue
            scope = str(row.get("scope") or "")
            scope_label = f"[{scope}]" if scope else ""
            path_display = str(row.get("path_display") or row.get("path") or "")
            if not row.get("exists"):
                path_display = f"{path_display} (missing)"
            lines.append(
                _format_table_row(
                    [
                        scope_label,
                        str(row.get("server_count") or 0),
                        path_display,
                    ],
                    widths,
                ),
            )
    else:
        lines.append("  (none)")

    lines.append("")
    lines.append("=== skill directories ===")
    skill_dirs = overview.get("skill_directories")
    if isinstance(skill_dirs, list) and skill_dirs:
        widths = [11, 7, 48]
        lines.append(_format_table_row(["Scope", "Total", "Path"], widths))
        for row in skill_dirs:
            if not isinstance(row, dict):
                continue
            scope = str(row.get("scope") or "")
            scope_label = f"[{scope}]" if scope else ""
            path_display = str(row.get("path_display") or row.get("path") or "")
            if not row.get("exists"):
                path_display = f"{path_display} (missing)"
            lines.append(
                _format_table_row(
                    [
                        scope_label,
                        str(row.get("skill_count") or 0),
                        path_display,
                    ],
                    widths,
                ),
            )
    else:
        lines.append("  (none)")

    lines.append("")
    lines.append("=== troubleshooting ===")
    troubleshooting = overview.get("troubleshooting")
    if isinstance(troubleshooting, dict):
        lines.append(
            "catalog: "
            f"count={troubleshooting.get('catalog_tool_count')}  "
            f"tracked={troubleshooting.get('tracked_catalog_tool_count')}",
        )
        lines.append(f"tier_db: {troubleshooting.get('tier_state_db')}")
        lines.append(
            "db_entities: "
            f"tools={troubleshooting.get('db_tool_entities')}  "
            f"skills={troubleshooting.get('db_skill_entities')}",
        )
        sources = troubleshooting.get("configured_sources")
        if isinstance(sources, list) and sources:
            lines.append(f"sources: {', '.join(str(item) for item in sources)}")

    return "\n".join(lines).rstrip() + "\n"
