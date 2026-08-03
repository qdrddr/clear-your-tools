"""Load mcp-aggregator.yaml and per-agent mcpServers JSON."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_AGGREGATOR_PATH = Path("~/.config/cyt/mcp-aggregator.yaml")
DEFAULT_MCP_DIR = Path("~/.config/cyt/mcp")


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
    aggregator_path: Path
    agent_mcp_path: Path


def _expand(path: str | Path) -> Path:
    return Path(path).expanduser()


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


def load_mcp_servers(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {}
    servers = payload.get("mcpServers")
    return servers if isinstance(servers, dict) else {}


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
) -> AggregatorConfig:
    raw = load_aggregator_yaml(aggregator_path)
    resolved_agent = resolve_agent_name(raw, agent)
    agent_path = agent_mcp_config_path(raw, resolved_agent)
    transport = str(raw.get("transport", "stdio")).strip().lower() or "stdio"
    if transport not in {"stdio", "http"}:
        transport = "stdio"
    codex_flag = bool(raw.get("codex_stubs_include_description", True))
    include_desc = codex_flag if resolved_agent == "codex" else False
    return AggregatorConfig(
        agent=resolved_agent,
        mcp_servers=load_mcp_servers(agent_path),
        transport=transport,
        http=load_http_settings(raw),
        codex_stubs_include_description=include_desc,
        aggregator_path=_expand(aggregator_path or DEFAULT_AGGREGATOR_PATH),
        agent_mcp_path=agent_path,
    )
