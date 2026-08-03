"""Wizard helpers for cyt-mcp aggregator setup and agent MCP migration."""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from typing import Any

DEFAULT_AGGREGATOR_PATH = Path("~/.config/cyt/mcp-aggregator.yaml")
DEFAULT_MCP_DIR = Path("~/.config/cyt/mcp")

_AGENT_SOURCE_PATHS: dict[str, Path] = {
    "cursor": Path("~/.cursor/mcp.json"),
    "claude": Path("~/.claude.json"),
    "codex": Path("~/.codex/config.toml"),
}


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    replaced = False
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
        replaced = True
    finally:
        if not replaced:
            tmp.unlink(missing_ok=True)


def _extract_mcp_servers_from_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    servers = payload.get("mcpServers")
    return servers if isinstance(servers, dict) else {}


def migrate_agent_backends(agent: str) -> Path:
    """Copy existing agent MCP servers into ~/.config/cyt/mcp/<agent>.json."""
    agent = agent.strip() or "cursor"
    target = DEFAULT_MCP_DIR.expanduser() / f"{agent}.json"
    source_path = _AGENT_SOURCE_PATHS.get(agent, Path("~/.cursor/mcp.json")).expanduser()
    servers = _extract_mcp_servers_from_json(source_path)
    if not servers:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.is_file():
            _atomic_write_text(target, json.dumps({"mcpServers": {}}, indent=2) + "\n")
        return target
    payload = {"mcpServers": servers}
    _atomic_write_text(target, json.dumps(payload, indent=2) + "\n")
    print(f"Migrated backend MCP servers to {target}", file=sys.stderr)
    return target


def write_mcp_aggregator_yaml(agent: str, *, backends_path: Path | None = None) -> Path:
    agent = agent.strip() or "cursor"
    path = DEFAULT_AGGREGATOR_PATH.expanduser()
    backends = backends_path or (DEFAULT_MCP_DIR.expanduser() / f"{agent}.json")
    lines = [
        f"default_agent: {agent}",
        "agents:",
        f"  cursor: {DEFAULT_MCP_DIR.expanduser() / 'cursor.json'}",
        f"  claude: {DEFAULT_MCP_DIR.expanduser() / 'claude.json'}",
        f"  codex: {DEFAULT_MCP_DIR.expanduser() / 'codex.json'}",
        "transport: stdio",
        "http:",
        "  host: 127.0.0.1",
        "  port: 8765",
        "  mcp_path: /mcp",
        "  catalog_path: /catalog",
        "codex_stubs_include_description: true",
        "",
    ]
    _atomic_write_text(path, "\n".join(lines))
    print(f"Wrote {path} (agent mapping includes {backends})", file=sys.stderr)
    return path


def write_agent_cyt_mcp_entry(agent: str) -> None:
    agent = agent.strip() or "cursor"
    source_path = _AGENT_SOURCE_PATHS.get(agent)
    if source_path is None:
        return
    path = source_path.expanduser()
    if agent == "codex":
        print(
            "Codex MCP config lives in ~/.codex/config.toml; "
            "add [mcp_servers.cyt-mcp] via cyt-client pairing or manually.",
            file=sys.stderr,
        )
        return
    payload = {
        "mcpServers": {
            "cyt-mcp": {
                "command": "cyt-mcp",
                "args": ["--agent", agent],
            },
        },
    }
    _atomic_write_text(path, json.dumps(payload, indent=2) + "\n")
    print(f"Wrote cyt-mcp entry to {path}", file=sys.stderr)


def setup_cyt_mcp_for_agent(agent: str) -> None:
    backends = migrate_agent_backends(agent)
    write_mcp_aggregator_yaml(agent, backends_path=backends)
    write_agent_cyt_mcp_entry(agent)
