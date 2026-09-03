"""Load mcp-aggregator.yaml and per-agent mcpServers JSON."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_AGGREGATOR_PATH = Path("~/.config/cyt/mcp-aggregator.yaml")
DEFAULT_MCP_DIR = Path("~/.config/cyt/mcp")

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


def agent_mcp_config_path(raw: dict[str, Any], agent: str) -> Path:
    agents = raw.get("agents")
    if isinstance(agents, dict):
        mapped = agents.get(agent)
        if isinstance(mapped, str) and mapped.strip():
            return _expand(mapped)
    return DEFAULT_MCP_DIR.expanduser() / f"{agent}.json"


def is_mcp_server_enabled(spec: object) -> bool:
    """Return False when a Cursor-style MCP server entry is explicitly disabled."""
    if not isinstance(spec, dict):
        return False
    enabled = spec.get("enabled", True)
    if isinstance(enabled, str):
        return enabled.strip().lower() not in {"false", "0", "no", "off"}
    return bool(enabled)


def _backend_server_spec(spec: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in spec.items() if key != "enabled"}


def load_mcp_servers(path: Path, *, workspace_folder: Path | None = None) -> dict[str, Any]:
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
        if not is_mcp_server_enabled(spec):
            continue
        if not isinstance(spec, dict):
            continue
        loaded[name] = expand_mcp_spec(
            _backend_server_spec(spec),
            workspace_folder=workspace_folder,
        )
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


def load_aggregator_config(
    *,
    agent: str | None = None,
    aggregator_path: Path | None = None,
    workspace_folder: Path | None = None,
) -> AggregatorConfig:
    raw = load_aggregator_yaml(aggregator_path)
    resolved_agent = resolve_agent_name(raw, agent)
    agent_path = agent_mcp_config_path(raw, resolved_agent)
    transport = str(raw.get("transport", "stdio")).strip().lower() or "stdio"
    if transport not in {"stdio", "http"}:
        transport = "stdio"
    codex_flag = bool(raw.get("codex_stubs_include_description", True))
    include_desc = codex_flag if resolved_agent == "codex" else False
    verify_only = bool(raw.get("verify_only", False))
    return AggregatorConfig(
        agent=resolved_agent,
        mcp_servers=load_mcp_servers(agent_path, workspace_folder=workspace_folder),
        transport=transport,
        http=load_http_settings(raw),
        codex_stubs_include_description=include_desc,
        verify_only=verify_only,
        aggregator_path=_expand(aggregator_path or DEFAULT_AGGREGATOR_PATH),
        agent_mcp_path=agent_path,
    )
