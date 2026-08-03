"""Wizard helpers for cyt-mcp aggregator setup and agent MCP migration."""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from typing import Any

from cyt.hook.cli_invocation import (
    HookCliInvocation,
    cyt_mcp_mcp_server_entry,
    detect_hook_cli_invocation,
)
from cyt.proxy.setup_wizard import _prompt
from cyt_client.mcp_entry import (
    CYT_MCP_SERVER_KEY,
    CytMcpTransport,
    backend_mcp_servers,
    codex_cyt_mcp_toml_block,
    cyt_mcp_http_catalog_url,
    normalize_cyt_mcp_transport,
)

DEFAULT_AGGREGATOR_PATH = Path("~/.config/cyt/mcp-aggregator.yaml")
DEFAULT_MCP_DIR = Path("~/.config/cyt/mcp")
DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8765
DEFAULT_MCP_PATH = "/mcp"
DEFAULT_CATALOG_PATH = "/catalog"

_AGENT_SOURCE_PATHS: dict[str, Path] = {
    "cursor": Path("~/.cursor/mcp.json"),
    "claude": Path("~/.claude.json"),
    "codex": Path("~/.codex/config.toml"),
}


def prompt_cyt_mcp_transport(*, default: CytMcpTransport = "stdio") -> CytMcpTransport:
    while True:
        raw = _prompt("cyt-mcp frontend transport (stdio, http)", default).strip().lower()
        if raw in {"stdio", "http"}:
            return normalize_cyt_mcp_transport(raw)
        print("Enter stdio or http.", file=sys.stderr)


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
    servers = backend_mcp_servers(_extract_mcp_servers_from_json(source_path))
    if not servers:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.is_file():
            _atomic_write_text(target, json.dumps({"mcpServers": {}}, indent=2) + "\n")
        return target
    payload = {"mcpServers": servers}
    _atomic_write_text(target, json.dumps(payload, indent=2) + "\n")
    print(f"Migrated backend MCP servers to {target}", file=sys.stderr)
    return target


def write_mcp_aggregator_yaml(
    agent: str,
    *,
    backends_path: Path | None = None,
    transport: CytMcpTransport = "stdio",
) -> Path:
    agent = agent.strip() or "cursor"
    path = DEFAULT_AGGREGATOR_PATH.expanduser()
    backends = backends_path or (DEFAULT_MCP_DIR.expanduser() / f"{agent}.json")
    lines = [
        f"default_agent: {agent}",
        "agents:",
        f"  cursor: {DEFAULT_MCP_DIR.expanduser() / 'cursor.json'}",
        f"  claude: {DEFAULT_MCP_DIR.expanduser() / 'claude.json'}",
        f"  codex: {DEFAULT_MCP_DIR.expanduser() / 'codex.json'}",
        f"transport: {transport}",
        "http:",
        f"  host: {DEFAULT_HTTP_HOST}",
        f"  port: {DEFAULT_HTTP_PORT}",
        f"  mcp_path: {DEFAULT_MCP_PATH}",
        f"  catalog_path: {DEFAULT_CATALOG_PATH}",
        "codex_stubs_include_description: true",
        "",
    ]
    _atomic_write_text(path, "\n".join(lines))
    print(f"Wrote {path} (agent mapping includes {backends})", file=sys.stderr)
    return path


def cyt_mcp_hook_settings_overlay(
    *,
    transport: CytMcpTransport,
    agent: str,
) -> dict[str, Any]:
    settings: dict[str, Any] = {"agent": agent.strip() or "cursor"}
    if transport == "http":
        settings["catalog_url"] = cyt_mcp_http_catalog_url()
    return settings


def _write_codex_cyt_mcp_entry(path: Path, agent: str, entry: dict[str, Any]) -> None:
    if path.is_file():
        text = path.read_text(encoding="utf-8")
    else:
        text = ""
    marker = "[mcp_servers.cyt-mcp]"
    block = codex_cyt_mcp_toml_block(agent, entry)
    if marker in text:
        before, _, after = text.partition(marker)
        next_section = after.find("\n[mcp_servers.")
        if next_section >= 0:
            text = before.rstrip() + after[next_section:]
        else:
            text = before.rstrip() + "\n"
    elif "cyt-mcp" in text and block.strip() in text:
        return
    _atomic_write_text(path, text.rstrip() + block)
    print(f"Wrote cyt-mcp entry to {path}", file=sys.stderr)


def _write_json_cyt_mcp_entry(
    path: Path,
    entry: dict[str, Any],
    *,
    agent: str,
    transport: CytMcpTransport,
    frontend_only: bool = False,
) -> None:
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
    else:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    if frontend_only:
        servers: dict[str, Any] = {CYT_MCP_SERVER_KEY: entry}
    else:
        existing = raw.get("mcpServers")
        if not isinstance(existing, dict):
            existing = {}
        servers = dict(existing)
        servers[CYT_MCP_SERVER_KEY] = entry
    raw["mcpServers"] = servers
    _atomic_write_text(path, json.dumps(raw, indent=2) + "\n")
    print(f"Wrote cyt-mcp entry to {path}", file=sys.stderr)
    if transport == "http":
        print(
            "Start cyt-mcp in HTTP mode separately, for example: "
            "cyt-mcp --agent "
            f"{agent} --transport http",
            file=sys.stderr,
        )


def write_agent_cyt_mcp_entry(
    agent: str,
    *,
    invocation: HookCliInvocation | None = None,
    transport: CytMcpTransport = "stdio",
    frontend_only: bool = False,
) -> None:
    agent = agent.strip() or "cursor"
    source_path = _AGENT_SOURCE_PATHS.get(agent)
    if source_path is None:
        return
    path = source_path.expanduser()
    entry = cyt_mcp_mcp_server_entry(agent, invocation=invocation, transport=transport)
    if agent == "codex":
        _write_codex_cyt_mcp_entry(path, agent, entry)
        return
    _write_json_cyt_mcp_entry(
        path,
        entry,
        agent=agent,
        transport=transport,
        frontend_only=frontend_only,
    )


def setup_cyt_mcp_for_agent(
    agent: str,
    *,
    invocation: HookCliInvocation | None = None,
    transport: CytMcpTransport = "stdio",
    migrate_backends: bool = True,
) -> None:
    resolved = invocation or detect_hook_cli_invocation()
    if migrate_backends:
        backends = migrate_agent_backends(agent)
        write_mcp_aggregator_yaml(agent, backends_path=backends, transport=transport)
    write_agent_cyt_mcp_entry(
        agent,
        invocation=resolved,
        transport=transport,
        frontend_only=migrate_backends,
    )
