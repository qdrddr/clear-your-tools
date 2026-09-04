"""Load mcp-aggregator.yaml and per-agent mcpServers JSON."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

logger = logging.getLogger(__name__)

DEFAULT_AGGREGATOR_PATH = Path("~/.config/cyt/mcp-aggregator.yaml")
DEFAULT_MCP_DIR = Path("~/.config/cyt/mcp")
GLOBAL_AGGREGATOR_PATH = DEFAULT_AGGREGATOR_PATH

CatalogScope = Literal["global", "workspace"]

_MCP_VAR_PATTERN = re.compile(r"\$\{(userHome|workspaceFolder|env:([^}]+))\}")

type McpJsonValue = str | int | float | bool | None | list[McpJsonValue] | dict[str, McpJsonValue]


@dataclass(frozen=True)
class HttpSettings:
    host: str
    port: int
    mcp_path: str
    catalog_path: str


@dataclass(frozen=True)
class AggregatorConfig:
    agent: str
    mcp_servers: dict[str, Any]
    transport: str
    http: HttpSettings
    codex_stubs_include_description: bool
    verify_only: bool
    aggregator_path: Path
    agent_mcp_path: Path
    catalog_scope: CatalogScope = "global"
    workspace_root: Path | None = None
    mcp_deny: tuple[str, ...] = ()


def _expand(path: str | Path) -> Path:
    return Path(path).expanduser()


def expand_mcp_value(value: str, *, workspace_folder: Path | None = None) -> str:
    """Expand Cursor-style MCP config variables in *value*."""
    workspace = (workspace_folder or Path.cwd()).resolve()
    home = Path.home()

    def repl(match: re.Match[str]) -> str:
        token = match.group(1)
        if token == "userHome":
            return str(home)
        if token == "workspaceFolder":
            return str(workspace)
        env_name = match.group(2)
        if env_name is not None:
            return os.environ.get(env_name, "")
        return match.group(0)

    expanded = _MCP_VAR_PATTERN.sub(repl, value)
    if expanded.startswith("~"):
        return str(Path(expanded).expanduser())
    return expanded


def expand_mcp_spec(
    spec: McpJsonValue,
    *,
    workspace_folder: Path | None = None,
) -> McpJsonValue:
    """Recursively expand MCP variables in a backend server spec."""
    if isinstance(spec, str):
        return expand_mcp_value(spec, workspace_folder=workspace_folder)
    if isinstance(spec, list):
        return [expand_mcp_spec(item, workspace_folder=workspace_folder) for item in spec]
    if isinstance(spec, dict):
        return {
            key: expand_mcp_spec(item, workspace_folder=workspace_folder)
            for key, item in spec.items()
        }
    return spec


def load_aggregator_yaml(path: Path | None = None) -> dict[str, Any]:
    resolved = _expand(path or DEFAULT_AGGREGATOR_PATH)
    if not resolved.is_file():
        return {}
    raw = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def resolve_agent_name(raw: dict[str, Any], explicit: str | None) -> str:
    if explicit and explicit.strip():
        return explicit.strip()
    default = raw.get("default_agent")
    if isinstance(default, str) and default.strip():
        return default.strip()
    raise ValueError("--agent is required when default_agent is not set in mcp-aggregator.yaml")


def _resolve_yaml_path(value: str, *, relative_to: Path | None) -> Path:
    """Resolve a path from mcp-aggregator.yaml (absolute, ~/, or relative to *relative_to*)."""
    text = value.strip()
    if text.startswith("~"):
        return Path(text).expanduser().resolve()
    candidate = Path(text)
    if candidate.is_absolute():
        return candidate.resolve()
    if relative_to is not None:
        return (relative_to / text).resolve()
    return candidate.expanduser().resolve()


def agent_mcp_config_path(
    raw: dict[str, Any],
    agent: str,
    *,
    aggregator_path: Path | None = None,
) -> Path:
    agents = raw.get("agents")
    if isinstance(agents, dict):
        mapped = agents.get(agent)
        if isinstance(mapped, str) and mapped.strip():
            relative_to = None
            if aggregator_path is not None:
                relative_to = aggregator_path.expanduser().resolve().parent
            return _resolve_yaml_path(mapped, relative_to=relative_to)
    return DEFAULT_MCP_DIR.expanduser() / f"{agent}.json"


def is_mcp_server_enabled(spec: object) -> bool:
    """Return False when a Cursor-style MCP server entry is explicitly disabled.

    Used for wizard migration and JSON sync only — runtime loading uses config.yaml deny.
    """
    if not isinstance(spec, dict):
        return False
    enabled = spec.get("enabled", True)
    if isinstance(enabled, str):
        return enabled.strip().lower() not in {"false", "0", "no", "off"}
    return bool(enabled)


def _backend_server_spec(spec: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in spec.items() if key != "enabled"}


def listed_mcp_server_names(path: Path) -> frozenset[str]:
    """Return every MCP server key in *path*, regardless of ``enabled`` state."""
    if not path.is_file():
        return frozenset()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return frozenset()
    if not isinstance(payload, dict):
        return frozenset()
    servers = payload.get("mcpServers")
    if not isinstance(servers, dict):
        return frozenset()
    return frozenset(str(name).strip() for name in servers if str(name).strip())


def _detect_workspace_for_global_exclusion(
    workspace_folder: Path | None,
) -> Path | None:
    if workspace_folder is not None:
        try:
            resolved = workspace_folder.expanduser().resolve()
            if resolved.is_dir():
                return resolved
        except OSError:
            pass
    from cyt.hook.install_scope import detect_workspace_root

    return detect_workspace_root()


def _workspace_claimed_server_names(
    agent: str,
    workspace_root: Path | None,
) -> frozenset[str]:
    if workspace_root is None:
        return frozenset()
    from cyt.hook.install_scope import CytInstallScope

    scope = CytInstallScope(workspace_root=workspace_root)
    defs_path = scope.resolve_workspace_server_defs_path(agent)
    if defs_path is None:
        return frozenset()
    return listed_mcp_server_names(defs_path)


def _exclude_workspace_claimed_servers(
    servers: dict[str, Any],
    *,
    agent: str,
    workspace_folder: Path | None,
) -> dict[str, Any]:
    """Drop global servers that appear in workspace MCP defs so workspace owns them."""
    workspace_root = _detect_workspace_for_global_exclusion(workspace_folder)
    claimed = _workspace_claimed_server_names(agent, workspace_root)
    if not claimed:
        return servers
    excluded = sorted(name for name in servers if name in claimed)
    if excluded:
        logger.info(
            "cyt-mcp global: excluding MCP servers claimed by workspace defs: %s",
            ", ".join(excluded),
        )
    return {name: spec for name, spec in servers.items() if name not in claimed}


def load_mcp_servers(
    path: Path,
    *,
    workspace_folder: Path | None = None,
    deny_entries: tuple[str, ...] | list[str] | None = None,
) -> dict[str, Any]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {}
    servers = payload.get("mcpServers")
    if not isinstance(servers, dict):
        return {}
    loaded: dict[str, Any] = {}
    for name, spec in servers.items():
        if not isinstance(spec, dict):
            continue
        loaded[name] = expand_mcp_spec(
            _backend_server_spec(spec),
            workspace_folder=workspace_folder,
        )
    if deny_entries:
        from cyt.permissions.match import is_mcp_server_denied

        loaded = {
            name: spec
            for name, spec in loaded.items()
            if not is_mcp_server_denied(name, deny_entries)
        }
    return loaded


def load_http_settings(raw: dict[str, Any]) -> HttpSettings:
    http = raw.get("http")
    block = http if isinstance(http, dict) else {}
    host = str(block.get("host", "127.0.0.1"))
    port_raw = block.get("port", 8765)
    try:
        port = int(port_raw)
    except (TypeError, ValueError):
        port = 8765
    mcp_path = str(block.get("mcp_path", "/mcp")).strip() or "/mcp"
    catalog_path = str(block.get("catalog_path", "/catalog")).strip() or "/catalog"
    if not mcp_path.startswith("/"):
        mcp_path = f"/{mcp_path}"
    if not catalog_path.startswith("/"):
        catalog_path = f"/{catalog_path}"
    return HttpSettings(host=host, port=port, mcp_path=mcp_path, catalog_path=catalog_path)


def _is_workspace_aggregator_path(resolved: Path) -> bool:
    parts = {part.lower() for part in resolved.parts}
    if parts & {".agents", "agents"} and "cyt" in parts and "config" in parts:
        return True
    return any(
        agent_dir in parts and "cyt" in parts and "config" in parts
        for agent_dir in (".cursor", ".claude", ".codex")
    )


def _infer_catalog_scope(
    raw: dict[str, Any],
    aggregator_path: Path,
) -> CatalogScope:
    explicit = raw.get("catalog_scope")
    if isinstance(explicit, str) and explicit.strip().lower() == "workspace":
        return "workspace"
    if isinstance(explicit, str) and explicit.strip().lower() == "global":
        return "global"
    resolved = aggregator_path.resolve()
    global_path = GLOBAL_AGGREGATOR_PATH.expanduser().resolve()
    if resolved == global_path:
        return "global"
    if _is_workspace_aggregator_path(resolved):
        return "workspace"
    return "global"


def _resolve_global_agent_mcp_path(configured: Path, agent: str) -> Path:
    """Ensure global cyt-mcp loads user-scoped backend defs, not cwd-relative workspace paths."""
    canonical = DEFAULT_MCP_DIR.expanduser() / f"{agent.strip() or 'cursor'}.json"
    if configured.is_absolute():
        return configured
    logger.warning(
        "Global aggregator agents.%s uses relative path %s; using %s",
        agent,
        configured,
        canonical,
    )
    return canonical


def _global_default_agent_mcp_path(agent: str) -> Path:
    return (DEFAULT_MCP_DIR.expanduser() / f"{agent.strip() or 'cursor'}.json").resolve()


def _resolve_workspace_agent_mcp_path(
    configured: Path,
    agent: str,
    workspace_root: Path | None,
) -> Path:
    """Ensure workspace cyt-mcp loads repo-scoped backend defs, not global defaults."""
    if workspace_root is None:
        logger.warning(
            "Workspace catalog scope but workspace root could not be resolved; "
            "backend MCP defs may be incorrect",
        )
        return configured

    from cyt.hook.install_scope import CytInstallScope

    scope = CytInstallScope(workspace_root=workspace_root)
    resolved = scope.resolve_workspace_server_defs_path(agent)
    if resolved is not None:
        return resolved

    canonical = scope.workspace_server_defs_path(agent)
    if canonical is None:
        return configured

    try:
        configured_resolved = configured.expanduser().resolve()
    except OSError:
        configured_resolved = configured

    global_default = _global_default_agent_mcp_path(agent)
    if not configured.is_absolute() or configured_resolved == global_default:
        logger.warning(
            "Workspace aggregator agents.%s uses %s; using %s",
            agent,
            configured,
            canonical,
        )
        return canonical

    try:
        configured_resolved.relative_to(workspace_root.resolve())
    except ValueError:
        logger.warning(
            "Workspace aggregator agents.%s points outside workspace (%s); using %s",
            agent,
            configured,
            canonical,
        )
        return canonical

    return configured_resolved


def _resolve_mcp_deny(
    agent: str,
    *,
    catalog_scope: CatalogScope,
    workspace_root: Path | None,
) -> tuple[str, ...]:
    try:
        if catalog_scope == "global":
            from cyt.permissions.merge import effective_mcp_permissions_global_only

            return effective_mcp_permissions_global_only(agent=agent).deny
        from cyt.permissions.merge import effective_permissions

        effective = effective_permissions(agent=agent, workspace_root=workspace_root)
        return effective.mcp.deny
    except Exception:
        return ()


def _resolve_workspace_root_for_scope(
    scope: CatalogScope,
    *,
    workspace_folder: Path | None,
    aggregator_path: Path,
) -> Path | None:
    if scope != "workspace":
        return None
    if workspace_folder is not None:
        try:
            resolved = workspace_folder.expanduser().resolve()
            if resolved.is_dir():
                return resolved
        except OSError:
            pass
    # Walk up from aggregator: .../.agents/cyt/config/mcp-aggregator.yaml
    current = aggregator_path.expanduser().resolve().parent
    for _ in range(6):
        if (current / ".git").exists() or (current / ".cursor").exists():
            return current
        if current.parent == current:
            break
        current = current.parent
    cwd = Path.cwd()
    try:
        resolved_cwd = cwd.resolve()
        return resolved_cwd if resolved_cwd.is_dir() else None
    except OSError:
        return None


def load_aggregator_config(
    *,
    agent: str | None = None,
    aggregator_path: Path | None = None,
    workspace_folder: Path | None = None,
) -> AggregatorConfig:
    raw = load_aggregator_yaml(aggregator_path)
    resolved_agent = resolve_agent_name(raw, agent)
    resolved_agg_path = _expand(aggregator_path or DEFAULT_AGGREGATOR_PATH)
    agent_path = agent_mcp_config_path(
        raw,
        resolved_agent,
        aggregator_path=resolved_agg_path,
    )
    transport = str(raw.get("transport", "stdio")).strip().lower() or "stdio"
    if transport not in {"stdio", "http"}:
        transport = "stdio"
    codex_flag = bool(raw.get("codex_stubs_include_description", True))
    include_desc = codex_flag if resolved_agent == "codex" else False
    verify_only = bool(raw.get("verify_only", False))
    catalog_scope = _infer_catalog_scope(raw, resolved_agg_path)
    if catalog_scope == "global":
        agent_path = _resolve_global_agent_mcp_path(agent_path, resolved_agent)
    workspace_root = _resolve_workspace_root_for_scope(
        catalog_scope,
        workspace_folder=workspace_folder,
        aggregator_path=resolved_agg_path,
    )
    if catalog_scope == "workspace":
        agent_path = _resolve_workspace_agent_mcp_path(
            agent_path,
            resolved_agent,
            workspace_root,
        )
    mcp_deny = _resolve_mcp_deny(
        resolved_agent,
        catalog_scope=catalog_scope,
        workspace_root=workspace_root,
    )
    loaded_servers = load_mcp_servers(
        agent_path,
        workspace_folder=workspace_root or workspace_folder,
        deny_entries=mcp_deny,
    )
    if catalog_scope == "global":
        loaded_servers = _exclude_workspace_claimed_servers(
            loaded_servers,
            agent=resolved_agent,
            workspace_folder=workspace_folder,
        )
    return AggregatorConfig(
        agent=resolved_agent,
        mcp_servers=loaded_servers,
        transport=transport,
        http=load_http_settings(raw),
        codex_stubs_include_description=include_desc,
        verify_only=verify_only,
        aggregator_path=resolved_agg_path,
        agent_mcp_path=agent_path,
        catalog_scope=catalog_scope,
        workspace_root=workspace_root,
        mcp_deny=mcp_deny,
    )
