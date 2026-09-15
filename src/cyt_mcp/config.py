"""Load mcp-config.yaml and per-agent mcpServers JSON."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from cyt.migrations.mcp_config import (
    maybe_migrate_mcp_config_file,
    maybe_repair_stale_mcp_config_file,
    resolve_mcp_config_path,
)
from cyt_mcp.stub_catalog import RetainSpec, resolve_stub_name, resolve_stub_retain

logger = logging.getLogger(__name__)

DEFAULT_MCP_CONFIG_PATH = Path("~/.config/cyt/mcp-config.yaml")
DEFAULT_AGGREGATOR_PATH = DEFAULT_MCP_CONFIG_PATH  # deprecated alias
DEFAULT_MCP_DIR = Path("~/.config/cyt/mcp")
GLOBAL_MCP_CONFIG_PATH = DEFAULT_MCP_CONFIG_PATH
GLOBAL_AGGREGATOR_PATH = DEFAULT_MCP_CONFIG_PATH  # deprecated alias

CatalogScope = Literal["user", "workspace"]
ServerOrigin = Literal["user", "workspace"]

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
    stub_name: str
    stub_retain: RetainSpec
    codex_stubs_include_description: bool
    verify_only: bool
    aggregator_path: Path
    agent_mcp_path: Path
    catalog_scope: CatalogScope = "user"
    workspace_root: Path | None = None
    mcp_deny: tuple[str, ...] = ()
    server_origins: dict[str, ServerOrigin] = field(default_factory=dict)


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


def load_mcp_config_yaml(path: Path | None = None) -> dict[str, Any]:
    resolved = resolve_mcp_config_path(
        _expand(path or DEFAULT_MCP_CONFIG_PATH),
        default=_expand(path or DEFAULT_MCP_CONFIG_PATH),
    )
    maybe_migrate_mcp_config_file(resolved)
    maybe_repair_stale_mcp_config_file(resolved)
    if not resolved.is_file():
        return {}
    raw = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def load_aggregator_yaml(path: Path | None = None) -> dict[str, Any]:
    """Load MCP config YAML (``mcp-config.yaml``; legacy ``mcp-aggregator.yaml`` supported)."""
    return load_mcp_config_yaml(path)


def resolve_agent_name(raw: dict[str, Any], explicit: str | None) -> str:
    if explicit and explicit.strip():
        return explicit.strip()
    default = raw.get("default_agent")
    if isinstance(default, str) and default.strip():
        return default.strip()
    raise ValueError("--agent is required when default_agent is not set in mcp-config.yaml")


def _resolve_yaml_path(value: str, *, relative_to: Path | None) -> Path:
    """Resolve a path from mcp-config.yaml (absolute, ~/, or relative to *relative_to*)."""
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


def _detect_workspace_for_load(
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


def _resolve_workspace_server_defs_path(
    agent: str,
    workspace_root: Path | None,
) -> Path | None:
    if workspace_root is None:
        return None
    from cyt.hook.install_scope import CytInstallScope

    scope = CytInstallScope(workspace_root=workspace_root)
    return scope.resolve_workspace_server_defs_path(agent)


def _load_unified_mcp_servers(
    *,
    agent: str,
    workspace_root: Path | None,
    workspace_folder: Path | None,
) -> tuple[dict[str, Any], dict[str, ServerOrigin]]:
    """Load user + workspace backend defs separately, merge at runtime (workspace wins)."""
    from cyt.permissions.merge import (
        effective_mcp_permissions_global_only,
        effective_permissions,
    )

    ws_folder = workspace_root or workspace_folder
    user_path = _global_default_agent_mcp_path(agent)
    user_deny = effective_mcp_permissions_global_only(agent=agent).deny
    user_servers = load_mcp_servers(
        user_path,
        workspace_folder=ws_folder,
        deny_entries=user_deny,
    )
    origins: dict[str, ServerOrigin] = dict.fromkeys(user_servers, "user")

    if workspace_root is None:
        return user_servers, origins

    ws_defs = _resolve_workspace_server_defs_path(agent, workspace_root)
    ws_path = ws_defs if ws_defs is not None else Path()
    effective_deny = effective_permissions(agent=agent, workspace_root=workspace_root).mcp.deny
    workspace_servers = load_mcp_servers(
        ws_path,
        workspace_folder=workspace_root,
        deny_entries=effective_deny,
    )
    merged = dict(user_servers)
    merged.update(workspace_servers)
    for name in workspace_servers:
        origins[name] = "workspace"
    return merged, origins


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
    if isinstance(explicit, str):
        lowered = explicit.strip().lower()
        if lowered == "workspace":
            return "workspace"
        if lowered in {"user", "global"}:
            return "user"
    resolved = aggregator_path.resolve()
    user_path = GLOBAL_MCP_CONFIG_PATH.expanduser().resolve()
    if resolved == user_path:
        return "user"
    if _is_workspace_aggregator_path(resolved):
        return "workspace"
    return "user"


def _resolve_user_agent_mcp_path(configured: Path, agent: str) -> Path:
    """Ensure user cyt-mcp metadata points at user-scoped backend defs."""
    from cyt.migrations.mcp_config import _is_ephemeral_path

    canonical = _global_default_agent_mcp_path(agent)
    if not configured.is_absolute():
        logger.warning(
            "User aggregator agents.%s uses relative path %s; using %s",
            agent,
            configured,
            canonical,
        )
        return canonical
    try:
        configured_resolved = configured.expanduser().resolve()
    except OSError:
        return canonical
    user_dir = DEFAULT_MCP_DIR.expanduser().resolve()
    if configured_resolved == canonical:
        return configured_resolved
    if _is_ephemeral_path(configured_resolved):
        logger.warning(
            "User aggregator agents.%s uses ephemeral path %s; using %s",
            agent,
            configured,
            canonical,
        )
        return canonical
    try:
        configured_resolved.relative_to(user_dir)
        return configured_resolved
    except ValueError:
        logger.warning(
            "User aggregator agents.%s points outside user MCP defs (%s); using %s",
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
        if workspace_root is not None:
            from cyt.permissions.merge import effective_permissions

            return effective_permissions(agent=agent, workspace_root=workspace_root).mcp.deny
        from cyt.permissions.merge import effective_mcp_permissions_global_only

        return effective_mcp_permissions_global_only(agent=agent).deny
    except Exception:
        return ()


def reload_mcp_deny(config: AggregatorConfig) -> tuple[str, ...]:
    """Re-read merged MCP deny list from on-disk permission overlays."""
    return _resolve_mcp_deny(
        config.agent,
        catalog_scope=config.catalog_scope,
        workspace_root=config.workspace_root,
    )


def _effective_workspace_folder(workspace_folder: Path | None) -> Path:
    if workspace_folder is not None:
        try:
            resolved = workspace_folder.expanduser().resolve()
            if resolved.is_dir():
                return resolved
        except OSError:
            pass
    try:
        return Path.cwd().resolve()
    except OSError:
        return Path.cwd()


def _resolve_aggregator_cli_path(
    aggregator_path: Path | None,
    *,
    workspace_folder: Path | None,
) -> Path:
    """Resolve CLI ``--config`` path, expanding Cursor-style ``${workspaceFolder}`` tokens."""
    folder = _effective_workspace_folder(workspace_folder)
    default = DEFAULT_MCP_CONFIG_PATH.expanduser()
    if aggregator_path is None:
        return default
    text = str(aggregator_path).strip()
    if not text:
        return default
    expanded = expand_mcp_value(text, workspace_folder=folder)
    return Path(expanded)


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
    # Walk up from config: .../.agents/cyt/config/mcp-config.yaml
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
    effective_workspace = _effective_workspace_folder(workspace_folder)
    resolved_agg_input = _resolve_aggregator_cli_path(
        aggregator_path,
        workspace_folder=effective_workspace,
    )
    resolved_agg_path = resolve_mcp_config_path(
        resolved_agg_input,
        default=DEFAULT_MCP_CONFIG_PATH.expanduser(),
    )
    raw = load_mcp_config_yaml(resolved_agg_path)
    resolved_agent = resolve_agent_name(raw, agent)
    agent_path = agent_mcp_config_path(
        raw,
        resolved_agent,
        aggregator_path=resolved_agg_path,
    )
    transport = str(raw.get("transport", "stdio")).strip().lower() or "stdio"
    if transport not in {"stdio", "http"}:
        transport = "stdio"
    stub_name = resolve_stub_name(raw, resolved_agent)
    stub_retain = resolve_stub_retain(raw, resolved_agent)
    include_desc = "description" in stub_retain.get("tool", ["name"])
    verify_only = bool(raw.get("verify_only", False))
    catalog_scope = _infer_catalog_scope(raw, resolved_agg_path)
    if catalog_scope == "user":
        agent_path = _resolve_user_agent_mcp_path(agent_path, resolved_agent)
    workspace_root = _resolve_workspace_root_for_scope(
        catalog_scope,
        workspace_folder=effective_workspace,
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
    loaded_servers, server_origins = _load_unified_mcp_servers(
        agent=resolved_agent,
        workspace_root=workspace_root,
        workspace_folder=effective_workspace,
    )
    return AggregatorConfig(
        agent=resolved_agent,
        mcp_servers=loaded_servers,
        transport=transport,
        http=load_http_settings(raw),
        stub_name=stub_name,
        stub_retain=stub_retain,
        codex_stubs_include_description=include_desc,
        verify_only=verify_only,
        aggregator_path=resolved_agg_path,
        agent_mcp_path=agent_path,
        catalog_scope="workspace" if workspace_root is not None else catalog_scope,
        workspace_root=workspace_root,
        mcp_deny=mcp_deny,
        server_origins=server_origins,
    )


def load_known_mcp_server_keys(
    *,
    agent: str | None = None,
    workspace_folder: Path | None = None,
    project_root: Path | str | None = None,
) -> list[str]:
    """Return MCP server keys for wire-name splitting, sorted longest-first.

    When *project_root* is set, return every backend server configured for that
    git project (global user MCP defs plus ``.agents/cyt/config/mcp/<agent>.json``).
    Examples use one project-scoped pool; this does not split user vs workspace origin.
    """
    resolved_agent = agent or "cursor"
    try:
        if project_root is not None:
            root = Path(project_root).expanduser().resolve()
            if root.is_dir():
                merged, _origins = _load_unified_mcp_servers(
                    agent=resolved_agent,
                    workspace_root=root,
                    workspace_folder=workspace_folder or root,
                )
                keys = [str(key).strip() for key in merged if str(key).strip()]
                return sorted(set(keys), key=len, reverse=True)
        config = load_aggregator_config(agent=agent, workspace_folder=workspace_folder)
        keys = [str(key).strip() for key in config.mcp_servers if str(key).strip()]
    except (OSError, ValueError, yaml.YAMLError, json.JSONDecodeError):
        return []
    return sorted(set(keys), key=len, reverse=True)


def load_known_mcp_server_keys_for_projects(
    project_roots: list[str | Path],
    *,
    agent: str | None = None,
) -> list[str]:
    """Union MCP server keys for all git projects stored in the examples DB."""
    keys: set[str] = set()
    for root in project_roots:
        text = str(root or "").strip()
        if not text:
            continue
        keys.update(load_known_mcp_server_keys(agent=agent, project_root=text))
    if keys:
        return sorted(keys, key=len, reverse=True)
    return load_known_mcp_server_keys(agent=agent)


_BASIC_STUB_RETAIN: RetainSpec = {
    "tool": ["name"],
    "required_properties": [],
    "optional_properties": [],
}


def sample_aggregator_config(
    *,
    agent: str = "cursor",
    stub_name: str = "basic",
    stub_retain: RetainSpec | None = None,
    codex_stubs_include_description: bool = False,
    aggregator_path: Path | None = None,
    mcp_servers: dict[str, Any] | None = None,
    catalog_scope: CatalogScope = "user",
    workspace_root: Path | None = None,
    verify_only: bool = False,
    transport: str = "stdio",
    mcp_deny: tuple[str, ...] = (),
    server_origins: dict[str, ServerOrigin] | None = None,
) -> AggregatorConfig:
    """Build a minimal :class:`AggregatorConfig` for unit tests."""
    retain = stub_retain if stub_retain is not None else _BASIC_STUB_RETAIN
    origins = server_origins if server_origins is not None else {}
    effective_scope: CatalogScope = "workspace" if workspace_root is not None else catalog_scope
    return AggregatorConfig(
        agent=agent,
        mcp_servers={} if mcp_servers is None else mcp_servers,
        transport=transport,
        http=HttpSettings(
            host="127.0.0.1",
            port=8765,
            mcp_path="/mcp",
            catalog_path="/catalog",
        ),
        stub_name=stub_name,
        stub_retain=retain,
        codex_stubs_include_description=codex_stubs_include_description,
        verify_only=verify_only,
        aggregator_path=aggregator_path or DEFAULT_MCP_CONFIG_PATH,
        agent_mcp_path=DEFAULT_MCP_DIR / f"{agent}.json",
        catalog_scope=effective_scope,
        workspace_root=workspace_root,
        mcp_deny=mcp_deny,
        server_origins=origins,
    )
