"""Resolve user/workspace scope and on-disk paths for tier status entities."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

EntityScope = Literal["user", "workspace"]

_MCP_CONFIG_CANDIDATE_KEYS = (
    "mcpServers",
    "servers",
)


def parse_tool_entity_id(entity_id: str) -> tuple[str, str]:
    text = str(entity_id or "").strip()
    if ":" in text:
        source, name = text.split(":", 1)
        return source.strip() or "unknown", name.strip()
    return "unknown", text


def _normalize_server_name(name: str) -> str:
    return str(name or "").strip().lstrip("@")


def _resolve_path(path: Path) -> Path:
    try:
        return path.expanduser().resolve()
    except OSError:
        return path.expanduser()


def _server_names_in_mcp_config(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return set()
    if not isinstance(payload, dict):
        return set()
    for key in _MCP_CONFIG_CANDIDATE_KEYS:
        servers = payload.get(key)
        if isinstance(servers, dict):
            return {str(name).strip() for name in servers if str(name).strip()}
    return set()


def _mcp_server_line_in_config(path: Path, server: str) -> int | None:
    """Return 1-based line number of *server* key in an MCP JSON config file."""
    if not path.is_file():
        return None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    key = re.escape(json.dumps(server))
    pattern = re.compile(rf"{key}\s*:")
    for lineno, line in enumerate(lines, start=1):
        if pattern.search(line):
            return lineno
    return None


def _mcp_config_candidates(
    *,
    agent: str,
    workspace_root: Path | None,
) -> list[tuple[EntityScope, Path]]:
    from cyt.hook.install_scope import CytInstallScope

    install = CytInstallScope(workspace_root=workspace_root)
    candidates: list[tuple[EntityScope, Path]] = []
    if install.has_workspace:
        ws_agent = install.workspace_agent_mcp_path(agent)
        if ws_agent is not None:
            candidates.append(("workspace", ws_agent))
        ws_defs = install.resolve_workspace_server_defs_path(agent)
        if ws_defs is not None:
            candidates.append(("workspace", ws_defs))
    candidates.append(("user", install.user_agent_mcp_path(agent)))
    candidates.append(("user", install.user_server_defs_path(agent)))
    return candidates


def resolve_mcp_server_origin(
    server: str,
    *,
    agent: str = "cursor",
    workspace_root: Path | None = None,
) -> tuple[EntityScope | None, str | None, int | None]:
    """Return (scope, config_path, line) when *server* is declared in MCP JSON."""
    normalized = _normalize_server_name(server)
    if not normalized:
        return None, None, None
    seen: set[str] = set()
    for scope, path in _mcp_config_candidates(agent=agent, workspace_root=workspace_root):
        resolved = _resolve_path(path)
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        if normalized in _server_names_in_mcp_config(resolved):
            return scope, key, _mcp_server_line_in_config(resolved, normalized)
    return None, None, None


def format_source_path_display(
    source_path: str | None,
    *,
    source_line: int | None = None,
    include_line: bool = False,
) -> str | None:
    if not source_path:
        return None
    if include_line and isinstance(source_line, int) and source_line > 0:
        return f"{source_path}:L{source_line}"
    return source_path


def resolve_aggregator_config_origin(
    *,
    workspace_root: Path | None,
    agent: str = "cursor",
) -> tuple[EntityScope | None, str | None]:
    from cyt.hook.install_scope import CytInstallScope
    from cyt_mcp.config import GLOBAL_MCP_CONFIG_PATH, _infer_catalog_scope, load_mcp_config_yaml

    install = CytInstallScope(workspace_root=workspace_root)
    candidates: list[Path] = []
    ws_path = install.resolve_workspace_mcp_config_path(agent)
    if ws_path is not None:
        candidates.append(ws_path)
    candidates.append(GLOBAL_MCP_CONFIG_PATH)

    seen: set[str] = set()
    for path in candidates:
        resolved = _resolve_path(path)
        key = str(resolved)
        if key in seen or not resolved.is_file():
            continue
        seen.add(key)
        raw = load_mcp_config_yaml(resolved)
        scope = _infer_catalog_scope(raw, resolved)
        return scope, key
    return None, None


def _mcpc_session_from_tool_name(tool_name: str) -> str | None:
    text = str(tool_name or "").strip()
    if not text.startswith("@") or "/" not in text:
        return None
    session, _, _rest = text.partition("/")
    return session.strip() or None


def _read_mcpc_catalog_payload(config: dict[str, Any]) -> dict[str, Any] | None:
    try:
        from cyt.mcpc.catalog_disk import normalize_mcpc_executable_slug, read_disk_catalog
        from cyt.mcpc.runtime import tools_hook_mcpc_executable

        slug = normalize_mcpc_executable_slug(tools_hook_mcpc_executable(config))
        payload = read_disk_catalog(slug)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _server_name_from_mcpc_catalog(
    payload: dict[str, Any],
    session: str,
    tool_name: str,
) -> str | None:
    sessions = payload.get("sessions")
    if isinstance(sessions, dict):
        meta = sessions.get(session)
        if isinstance(meta, dict):
            server_name = str(meta.get("server_name") or "").strip()
            if server_name:
                return server_name
    tools = payload.get("tools")
    if not isinstance(tools, list):
        return None
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        if str(tool.get("name") or "").strip() != tool_name:
            continue
        server_name = str(tool.get("server_name") or "").strip()
        if server_name:
            return server_name
    return None


def _mcpc_server_name(tool_name: str, config: dict[str, Any] | None) -> str | None:
    session = _mcpc_session_from_tool_name(tool_name)
    if session is None:
        return None
    if config is None:
        return _normalize_server_name(session)
    payload = _read_mcpc_catalog_payload(config)
    if payload is not None:
        server_name = _server_name_from_mcpc_catalog(payload, session, tool_name)
        if server_name:
            return server_name
    return _normalize_server_name(session)


def infer_entity_mcp_server(entity: dict[str, Any]) -> str | None:
    """Return backend MCP server name for a tool entity."""
    server = entity.get("mcp_server")
    if isinstance(server, str) and server.strip():
        return server.strip()
    if str(entity.get("kind") or "") != "tool":
        return None
    entity_id = str(entity.get("entity_id") or "")
    _source, tool_name = parse_tool_entity_id(entity_id)
    catalog_source = resolve_tool_catalog_source_name(entity_id, tool_name)
    if catalog_source == "mcpc":
        return _mcpc_server_name(tool_name, None)
    if catalog_source == "cyt_mcp":
        from cyt.permissions.match import split_catalog_tool_name

        parts = split_catalog_tool_name(tool_name)
        if parts is not None:
            return parts[0]
    return None


def resolve_tool_catalog_source_name(entity_id: str, tool_name: str) -> str:
    source, _name = parse_tool_entity_id(entity_id)
    if source != "unknown":
        return source
    from cyt.tiers.adapters.tools import resolve_tool_catalog_source

    return resolve_tool_catalog_source({"name": tool_name or _name})


def _resolve_tool_mcp_server(
    catalog_source: str,
    tool_name: str,
    config: dict[str, Any] | None,
) -> str | None:
    from cyt.permissions.match import split_catalog_tool_name
    from cyt.tool_examples.identity import resolve_mcp_server_and_tool

    if catalog_source == "mcpc":
        return _mcpc_server_name(tool_name, config)
    if catalog_source != "cyt_mcp":
        return None
    parts = split_catalog_tool_name(tool_name)
    if parts is not None:
        return parts[0]
    server, _bare = resolve_mcp_server_and_tool({"name": tool_name})
    return None if server == "unknown" else server


def _attach_mcp_server_origin_fields(
    fields: dict[str, Any],
    server: str,
    *,
    resolved_agent: str,
    workspace_root: Path | None,
) -> None:
    fields["mcp_server"] = server
    scope, path, line = resolve_mcp_server_origin(
        server,
        agent=resolved_agent,
        workspace_root=workspace_root,
    )
    if scope:
        fields["scope"] = scope
    if path:
        fields["source_path"] = path
    if line:
        fields["source_line"] = line


def _attach_aggregator_origin_fields(
    fields: dict[str, Any],
    *,
    resolved_agent: str,
    workspace_root: Path | None,
) -> None:
    scope, path = resolve_aggregator_config_origin(
        workspace_root=workspace_root,
        agent=resolved_agent,
    )
    if scope and "scope" not in fields:
        fields["scope"] = scope
    if path:
        fields["source_path"] = path


def resolve_tool_origin_fields(
    entity_id: str,
    *,
    config: dict[str, Any] | None = None,
    workspace_root: Path | None = None,
    agent: str | None = None,
) -> dict[str, Any]:
    from cyt.permissions.paths import resolve_inventory_agent

    _source, tool_name = parse_tool_entity_id(entity_id)
    catalog_source = resolve_tool_catalog_source_name(entity_id, tool_name)
    fields: dict[str, Any] = {"catalog_source": catalog_source}

    resolved_agent = resolve_inventory_agent(agent)
    server = _resolve_tool_mcp_server(catalog_source, tool_name, config)
    if server:
        _attach_mcp_server_origin_fields(
            fields,
            server,
            resolved_agent=resolved_agent,
            workspace_root=workspace_root,
        )

    if "source_path" not in fields:
        _attach_aggregator_origin_fields(
            fields,
            resolved_agent=resolved_agent,
            workspace_root=workspace_root,
        )

    return fields


def resolve_skill_scope(
    source_path: str | None,
    *,
    workspace_root: Path | None,
) -> EntityScope | None:
    if not source_path:
        return None
    try:
        resolved = Path(source_path).expanduser().resolve()
    except OSError:
        resolved = Path(source_path).expanduser()
    if workspace_root is not None:
        try:
            ws = workspace_root.expanduser().resolve()
            resolved.relative_to(ws)
            return "workspace"
        except (OSError, ValueError):
            pass
    return "user"


def resolve_skill_origin_fields(
    *,
    source_path: str | None,
    workspace_root: Path | None,
) -> dict[str, Any]:
    scope = resolve_skill_scope(source_path, workspace_root=workspace_root)
    fields: dict[str, Any] = {}
    if scope:
        fields["scope"] = scope
    return fields
