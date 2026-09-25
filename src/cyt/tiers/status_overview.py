"""Infrastructure overview payload and text formatting for ``tiers stats``."""

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
    for directory in resolve_skill_directories(
        config,
        agent=agent,
        workspace_root=workspace_root,
        include_platform_defaults=True,
    ):
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
    include_skills: bool = True,
    include_tools: bool = True,
) -> dict[str, Any]:
    from cyt.hook.workspace_config import set_hook_workspace_in_config
    from cyt.tiers.config import tier_state_db_path
    from cyt.tools.master_catalog import get_master_tool_catalog, master_catalog_health_snapshot

    scoped = set_hook_workspace_in_config(config, workspace_root) if workspace_root else config
    catalog_tools = get_master_tool_catalog(scoped, blocking=True) or []
    catalog_health = master_catalog_health_snapshot(scoped)
    catalog_scope_counts = _catalog_scope_counts(catalog_tools)
    mcp_config_files = _list_mcp_config_files(agent=agent, workspace_root=workspace_root)
    workspace_mcp_defs = any(
        row.get("scope") == "workspace" and row.get("exists") and row.get("kind") == "server_defs"
        for row in mcp_config_files
        if isinstance(row, dict)
    )

    tools_raw = status.get("tools")
    skills_raw = status.get("skills")
    tools_block: dict[str, Any] = tools_raw if isinstance(tools_raw, dict) else {}
    skills_block: dict[str, Any] = skills_raw if isinstance(skills_raw, dict) else {}

    tool_histogram = tools_block.get("histogram")
    skill_histogram = skills_block.get("histogram")
    tool_histogram_values = tool_histogram.values() if isinstance(tool_histogram, dict) else ()
    skill_histogram_values = skill_histogram.values() if isinstance(skill_histogram, dict) else ()

    db_tool_count = sum(int(v) for v in tool_histogram_values if isinstance(v, int))
    db_skill_count = sum(int(v) for v in skill_histogram_values if isinstance(v, int))

    from cyt.tiers.status_statistics import build_tier_statistics

    tier_statistics = build_tier_statistics(
        status,
        catalog_tools=catalog_tools if include_tools else None,
        include_skills=include_skills,
        include_tools=include_tools,
    )

    tiers: dict[str, Any] = {}
    if include_tools:
        tiers["tools"] = {
            "mode": tools_block.get("mode"),
        }
    if include_skills:
        tiers["skills"] = {
            "mode": skills_block.get("mode"),
        }

    troubleshooting: dict[str, Any] = {
        "scoped_project_id": status.get("project_id"),
        "tier_state_db": _short_path(tier_state_db_path(scoped)),
    }
    if include_tools:
        troubleshooting.update(
            {
                "catalog_user_global_only": not workspace_mcp_defs,
                "configured_sources": catalog_health.get("configured_sources"),
                "catalog_tool_count": catalog_health.get("catalog_tool_count"),
                "catalog_user_tool_count": catalog_scope_counts.get("user", 0),
                "catalog_workspace_tool_count": catalog_scope_counts.get("workspace", 0),
                "catalog_unscoped_tool_count": catalog_scope_counts.get("unknown", 0),
                "tracked_catalog_tool_count": tools_block.get("tracked_catalog_tool_count"),
                "db_tool_entities": db_tool_count,
                **{
                    key: catalog_health[key]
                    for key in (
                        "catalog_age_seconds",
                        "composite_fingerprint_prefix",
                        "source_fingerprints",
                    )
                    if key in catalog_health
                },
            },
        )

    overview: dict[str, Any] = {
        "epoch": {
            "epoch_id": status.get("epoch_id"),
            "wake_cycle_id": status.get("wake_cycle_id"),
            "epoch_start_ms": status.get("epoch_start_ms"),
            "last_request_ms": status.get("last_request_ms"),
            "epoch_timeout_seconds": status.get("epoch_timeout_seconds"),
            "epoch_remaining_seconds": status.get("epoch_remaining_seconds"),
        },
        "tiers": tiers,
        "tier_statistics": tier_statistics,
        "troubleshooting": troubleshooting,
    }
    if include_tools:
        overview["mcp_servers"] = _list_mcp_servers(
            catalog_tools,
            agent=agent,
            workspace_root=workspace_root,
        )
        overview["mcp_config_files"] = mcp_config_files
    if include_skills:
        overview["skill_directories"] = _list_skill_directories(
            scoped,
            agent=agent,
            workspace_root=workspace_root,
        )
        troubleshooting = overview["troubleshooting"]
        if isinstance(troubleshooting, dict):
            troubleshooting["db_skill_entities"] = db_skill_count
    return overview


def format_duration_compact(seconds: int) -> str:
    """Format seconds as plain seconds (<60) or compact m:s (>=60)."""
    if seconds >= 60:
        minutes, remainder = divmod(seconds, 60)
        return f"{minutes}:{remainder:02d}"
    return str(seconds)


def _format_table_row(columns: list[str], widths: list[int]) -> str:
    parts: list[str] = []
    for column, width in zip(columns, widths, strict=True):
        text = column if len(column) <= width else column[: max(0, width - 3)] + "..."
        parts.append(text.ljust(width))
    return "  ".join(parts)


def _append_overview_epoch(lines: list[str], overview: dict[str, Any]) -> None:
    epoch = overview.get("epoch")
    if not isinstance(epoch, dict):
        return
    lines.append(
        f"epoch_id: {epoch.get('epoch_id')}  wake_cycle_id: {epoch.get('wake_cycle_id')}",
    )
    timeout_seconds = epoch.get("epoch_timeout_seconds")
    remaining_seconds = epoch.get("epoch_remaining_seconds")
    if isinstance(timeout_seconds, int) and isinstance(remaining_seconds, int):
        lines.append(
            "epoch_timeout: "
            f"{format_duration_compact(timeout_seconds)}  "
            "epoch_remaining: "
            f"{format_duration_compact(remaining_seconds)}",
        )
    if epoch.get("last_request_ms"):
        lines.append(
            f"epoch_start_ms: {epoch.get('epoch_start_ms')}  "
            f"last_request_ms: {epoch.get('last_request_ms')}",
        )


def _append_overview_tiers(lines: list[str], overview: dict[str, Any]) -> None:
    tiers = overview.get("tiers")
    if not isinstance(tiers, dict):
        return
    tools_tiers = tiers.get("tools")
    skills_tiers = tiers.get("skills")
    if isinstance(tools_tiers, dict):
        lines.append(f"tools: mode={tools_tiers.get('mode')}")
    if isinstance(skills_tiers, dict):
        lines.append(f"skills: mode={skills_tiers.get('mode')}")


def _catalog_scope_counts(tools: list[Any]) -> dict[str, int]:
    counts = {"user": 0, "workspace": 0, "unknown": 0}
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        scope = str(tool.get("cyt_catalog_scope") or "").strip().lower()
        if scope in {"user", "global"}:
            counts["user"] += 1
        elif scope == "workspace":
            counts["workspace"] += 1
        else:
            counts["unknown"] += 1
    return counts


def _scope_label(row: dict[str, Any]) -> str:
    scope = str(row.get("scope") or "")
    return f"[{scope}]" if scope else ""


def _append_overview_mcp_servers(lines: list[str], overview: dict[str, Any]) -> None:
    lines.append("")
    lines.append("=== mcp servers ===")
    servers = overview.get("mcp_servers")
    if not isinstance(servers, list) or not servers:
        lines.append("  (none)")
        return
    widths = [11, 22, 7, 44]
    lines.append(_format_table_row(["Scope", "Server", "Total", "Path"], widths))
    for row in servers:
        if not isinstance(row, dict):
            continue
        path_display = str(row.get("source_path_display") or row.get("source_path") or "")
        lines.append(
            _format_table_row(
                [
                    _scope_label(row),
                    str(row.get("name") or ""),
                    str(row.get("tool_count") or 0),
                    path_display,
                ],
                widths,
            ),
        )


def _append_overview_scoped_path_table(
    lines: list[str],
    *,
    title: str,
    rows: list[dict[str, Any]] | None,
    count_key: str,
) -> None:
    lines.append("")
    lines.append(title)
    if not isinstance(rows, list) or not rows:
        lines.append("  (none)")
        return
    widths = [11, 7, 48]
    lines.append(_format_table_row(["Scope", "Total", "Path"], widths))
    for row in rows:
        path_display = str(row.get("path_display") or row.get("path") or "")
        if not row.get("exists"):
            path_display = f"{path_display} (missing)"
        lines.append(
            _format_table_row(
                [
                    _scope_label(row),
                    str(row.get(count_key) or 0),
                    path_display,
                ],
                widths,
            ),
        )


def _append_overview_troubleshooting(lines: list[str], overview: dict[str, Any]) -> None:
    lines.append("")
    lines.append("=== troubleshooting ===")
    troubleshooting = overview.get("troubleshooting")
    if not isinstance(troubleshooting, dict):
        return
    scoped_project_id = troubleshooting.get("scoped_project_id")
    if scoped_project_id is not None:
        lines.append(f"scoped_project_id: {scoped_project_id}")
    if troubleshooting.get("catalog_user_global_only"):
        lines.append(
            "catalog_scope: user-global only (no workspace MCP server defs; "
            "histograms may match across repos)",
        )
    if "catalog_tool_count" in troubleshooting:
        catalog_line = (
            "catalog: "
            f"count={troubleshooting.get('catalog_tool_count')}  "
            f"tracked={troubleshooting.get('tracked_catalog_tool_count')}"
        )
        user_count = troubleshooting.get("catalog_user_tool_count")
        workspace_count = troubleshooting.get("catalog_workspace_tool_count")
        if isinstance(user_count, int) and isinstance(workspace_count, int):
            catalog_line += f"  user={user_count}  workspace={workspace_count}"
        lines.append(catalog_line)
        lines.append(
            "note: hook catalog excludes get-tool-definitions; MCP UI tool counts include it",
        )
    lines.append(f"tier_db: {troubleshooting.get('tier_state_db')}")
    db_entity_parts: list[str] = []
    if "db_tool_entities" in troubleshooting:
        db_entity_parts.append(f"tools={troubleshooting.get('db_tool_entities')}")
    if "db_skill_entities" in troubleshooting:
        db_entity_parts.append(f"skills={troubleshooting.get('db_skill_entities')}")
    if db_entity_parts:
        lines.append(f"db_entities: {'  '.join(db_entity_parts)}")
    sources = troubleshooting.get("configured_sources")
    if isinstance(sources, list) and sources:
        lines.append(f"sources: {', '.join(str(item) for item in sources)}")


def format_overview_text(payload: dict[str, Any], *, verbose: bool = False) -> str:
    from cyt.tiers.status_statistics import append_tier_statistics_tables
    from cyt.tiers.status_view import format_project_header

    lines = format_project_header(payload).splitlines()
    overview = payload.get("overview")
    if not isinstance(overview, dict):
        return "\n".join(lines).rstrip() + "\n"

    _append_overview_epoch(lines, overview)
    _append_overview_tiers(lines, overview)
    append_tier_statistics_tables(lines, overview, format_table_row=_format_table_row)

    if verbose:
        _append_overview_mcp_servers(lines, overview)
        _append_overview_scoped_path_table(
            lines,
            title="=== mcp config files ===",
            rows=overview.get("mcp_config_files"),
            count_key="server_count",
        )
        _append_overview_scoped_path_table(
            lines,
            title="=== skill directories ===",
            rows=overview.get("skill_directories"),
            count_key="skill_count",
        )
        _append_overview_troubleshooting(lines, overview)

    return "\n".join(lines).rstrip() + "\n"
