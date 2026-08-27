"""Build cyt-mcp MCP server entries (stdlib only)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal, cast

from cyt_client.hook_executable import (
    is_uv_run_dev_hook_command,
    repo_root_from_uv_run_hook_command,
)

INSTALLED_CYT_MCP_COMMAND = "cyt-mcp"
CYT_MCP_SERVER_KEY = "cyt-mcp"
CYT_MCP_SCRIPT_REL = "src/cyt_mcp/cli.py"
DEFAULT_AGGREGATOR_PATH = Path("~/.config/cyt/mcp-aggregator.yaml")
CytMcpTransport = Literal["stdio", "http"]
DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8765
DEFAULT_MCP_PATH = "/mcp"
DEFAULT_CATALOG_PATH = "/catalog"


def normalize_cyt_mcp_transport(value: str | None) -> CytMcpTransport:
    transport = str(value or "stdio").strip().lower()
    return "http" if transport == "http" else "stdio"


def cyt_mcp_http_mcp_url(
    *,
    host: str = DEFAULT_HTTP_HOST,
    port: int = DEFAULT_HTTP_PORT,
    mcp_path: str = DEFAULT_MCP_PATH,
) -> str:
    path = mcp_path if mcp_path.startswith("/") else f"/{mcp_path}"
    return f"http://{host}:{port}{path}"


def is_cyt_mcp_frontend_server(name: str, spec: object) -> bool:
    """Return True when an agent MCP server entry is the cyt-mcp frontend (not a backend)."""
    if str(name).strip() in {CYT_MCP_SERVER_KEY, "cyt_mcp"}:
        return True
    if not isinstance(spec, dict):
        return False
    spec_dict: dict[str, Any] = cast(dict[str, Any], spec)
    command = spec_dict.get("command")
    if command == INSTALLED_CYT_MCP_COMMAND:
        return True
    args = spec_dict.get("args")
    if command == "uv" and isinstance(args, list):
        joined = " ".join(str(arg) for arg in args)
        if CYT_MCP_SCRIPT_REL in joined or "cyt_mcp/cli.py" in joined:
            return True
    url = spec_dict.get("url")
    if isinstance(url, str):
        normalized = url.strip().rstrip("/")
        if normalized == cyt_mcp_http_mcp_url().rstrip("/"):
            return True
    return False


def backend_mcp_servers(servers: dict[str, Any]) -> dict[str, Any]:
    """Drop cyt-mcp frontend entries; keep real backend MCP servers for migration."""
    return {
        name: spec for name, spec in servers.items() if not is_cyt_mcp_frontend_server(name, spec)
    }


def cyt_mcp_http_catalog_url(
    *,
    host: str = DEFAULT_HTTP_HOST,
    port: int = DEFAULT_HTTP_PORT,
    catalog_path: str = DEFAULT_CATALOG_PATH,
) -> str:
    path = catalog_path if catalog_path.startswith("/") else f"/{catalog_path}"
    return f"http://{host}:{port}{path}"


def _parse_aggregator_scalar(raw: dict[str, str], key: str, default: str) -> str:
    value = raw.get(key, default).strip()
    return value or default


def load_aggregator_transport_settings(
    path: Path | None = None,
) -> tuple[CytMcpTransport, str, int, str, str]:
    resolved = (path or DEFAULT_AGGREGATOR_PATH).expanduser()
    scalars: dict[str, str] = {"transport": "stdio"}
    http_scalars: dict[str, str] = {}
    section: str | None = None
    if resolved.is_file():
        for line in resolved.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped == "http:":
                section = "http"
                continue
            if section != "http" and stripped.endswith(":") and not stripped.startswith("-"):
                section = None
            match = re.match(r"^([A-Za-z0-9_]+):\s*(.+)$", stripped)
            if not match:
                continue
            key, value = match.group(1), match.group(2).strip().strip("'\"")
            if section == "http":
                http_scalars[key] = value
            else:
                scalars[key] = value
    transport = normalize_cyt_mcp_transport(scalars.get("transport"))
    host = _parse_aggregator_scalar(http_scalars, "host", DEFAULT_HTTP_HOST)
    port_raw = http_scalars.get("port", str(DEFAULT_HTTP_PORT))
    try:
        port = int(port_raw)
    except (TypeError, ValueError):
        port = DEFAULT_HTTP_PORT
    mcp_path = _parse_aggregator_scalar(http_scalars, "mcp_path", DEFAULT_MCP_PATH)
    catalog_path = _parse_aggregator_scalar(http_scalars, "catalog_path", DEFAULT_CATALOG_PATH)
    return transport, host, port, mcp_path, catalog_path


def build_cyt_mcp_mcp_server_entry(
    agent: str,
    *,
    transport: CytMcpTransport = "stdio",
    dev_repo_root: Path | str | None = None,
    dev_script_rel: str | None = None,
    http_host: str = DEFAULT_HTTP_HOST,
    http_port: int = DEFAULT_HTTP_PORT,
    http_mcp_path: str = DEFAULT_MCP_PATH,
) -> dict[str, Any]:
    agent_name = agent.strip() or "cursor"
    if transport == "http":
        return {"url": cyt_mcp_http_mcp_url(host=http_host, port=http_port, mcp_path=http_mcp_path)}
    if dev_repo_root is not None and dev_script_rel:
        return {
            "command": "uv",
            "args": [
                "run",
                "--directory",
                str(dev_repo_root),
                dev_script_rel,
                "--agent",
                agent_name,
            ],
        }
    return {
        "command": INSTALLED_CYT_MCP_COMMAND,
        "args": ["--agent", agent_name],
    }


def _strip_env_prefix(command: str) -> str:
    text = command.strip()
    if not text:
        return text
    lowered = text.casefold()
    if lowered.startswith("cmd /c"):
        inner = text[6:].strip()
        if inner.startswith('"') and inner.endswith('"'):
            inner = inner[1:-1]
        resolved: str | None = None
        for part in inner.split("&&"):
            stripped = part.strip()
            if stripped.lower().startswith("set "):
                continue
            if stripped.lower().startswith("call "):
                return stripped[5:].strip().strip('"')
            resolved = stripped.strip('"')
        return resolved or inner.strip('"')
    if text.startswith("uv "):
        return text
    space = text.find(" ")
    if space <= 0 or "=" not in text[:space]:
        return text
    offset = space + 1
    return text[offset:].strip()


_WINDOWS_DEV_WRAPPER_SUFFIXES = (
    "cyt-client-dev.cmd",
    "cyt-hook-daemon-start-dev.cmd",
)


def _inner_command_from_windows_wrapper(command: str) -> str | None:
    normalized = command.strip().strip('"')
    if not normalized.casefold().endswith(".cmd"):
        return None
    path = Path(normalized)
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.casefold() == "@echo off":
            continue
        return stripped
    return None


def is_cyt_dev_hook_command(command: str) -> bool:
    normalized = _strip_env_prefix(command)
    if normalized.casefold().endswith(".cmd"):
        name = Path(normalized).name.casefold()
        if any(name.endswith(suffix) for suffix in _WINDOWS_DEV_WRAPPER_SUFFIXES):
            return True
        inner = _inner_command_from_windows_wrapper(normalized)
        if inner is not None:
            normalized = inner
    return is_uv_run_dev_hook_command(normalized)


def _iter_hook_commands(hooks_payload: dict[str, Any]) -> list[str]:
    hooks = hooks_payload.get("hooks")
    if not isinstance(hooks, dict):
        return []
    commands: list[str] = []
    for event_commands in hooks.values():
        if not isinstance(event_commands, list):
            continue
        for item in event_commands:
            if isinstance(item, dict):
                cmd = item.get("command")
                if isinstance(cmd, str):
                    commands.append(cmd)
    return commands


def dev_invocation_from_hooks_file(hooks_path: Path) -> tuple[Path, str] | None:
    if not hooks_path.is_file():
        return None
    try:
        payload = json.loads(hooks_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    for command in _iter_hook_commands(payload):
        resolved = _inner_command_from_windows_wrapper(command) or command
        if not is_cyt_dev_hook_command(resolved):
            continue
        repo = repo_root_from_uv_run_hook_command(_strip_env_prefix(resolved))
        if repo is None:
            continue
        if (repo / CYT_MCP_SCRIPT_REL).is_file():
            return repo, CYT_MCP_SCRIPT_REL
    return None


def dev_invocation_from_mcp_file(mcp_path: Path) -> tuple[Path, str] | None:
    if not mcp_path.is_file():
        return None
    try:
        payload = json.loads(mcp_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    servers = payload.get("mcpServers")
    if not isinstance(servers, dict):
        return None
    spec = servers.get(CYT_MCP_SERVER_KEY)
    if not isinstance(spec, dict):
        return None
    spec_dict: dict[str, Any] = cast(dict[str, Any], spec)
    command = spec_dict.get("command")
    args = spec_dict.get("args")
    if command != "uv" or not isinstance(args, list):
        return None
    joined = " ".join(str(arg) for arg in args)
    if CYT_MCP_SCRIPT_REL not in joined and "cyt_mcp/cli.py" not in joined:
        return None
    if len(args) >= 4 and args[0] == "run" and args[1] == "--directory":
        repo = Path(str(args[2]))
        if (repo / CYT_MCP_SCRIPT_REL).is_file():
            return repo, CYT_MCP_SCRIPT_REL
    return None


def mcp_entries_equivalent(existing: object, desired: dict[str, Any]) -> bool:
    """Compare MCP entries ignoring extra agent keys such as ``enabled``."""
    if not isinstance(existing, dict):
        return False
    existing_dict: dict[str, Any] = cast(dict[str, Any], existing)
    return all(existing_dict.get(key) == value for key, value in desired.items())


def codex_cyt_mcp_toml_block(agent: str, entry: dict[str, Any]) -> str:
    url = entry.get("url")
    if isinstance(url, str) and url.strip():
        return f'\n[mcp_servers.cyt-mcp]\nurl = "{url.strip()}"\n'
    command = str(entry.get("command", INSTALLED_CYT_MCP_COMMAND))
    args = entry.get("args")
    if not isinstance(args, list):
        args = ["--agent", agent]
    args_json = json.dumps(args)
    return f'\n[mcp_servers.cyt-mcp]\ncommand = "{command}"\nargs = {args_json}\n'
