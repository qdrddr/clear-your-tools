"""Bidirectional cyt-mcp / cyt-client config pairing (stdlib only, session lifecycle)."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from cyt_client.agent import infer_harness_agent
from cyt_client.config import resolve_config_path, tools_from_includes_cyt_mcp

_AGENT_MCP_PATHS: dict[str, Path] = {
    "cursor": Path("~/.cursor/mcp.json"),
    "claude": Path("~/.claude.json"),
    "codex": Path("~/.codex/config.toml"),
}

_AGENT_HOOK_PATHS: dict[str, Path] = {
    "cursor": Path("~/.cursor/hooks.json"),
    "claude": Path("~/.claude/settings.json"),
    "codex": Path("~/.codex/hooks.json"),
}

_CYT_MCP_SERVER_KEY = "cyt-mcp"
_REPAIRED_SESSIONS: set[tuple[str, str]] = set()


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


def _canonical_cyt_mcp_entry(agent: str) -> dict[str, Any]:
    return {
        "command": "cyt-mcp",
        "args": ["--agent", agent],
    }


def _ensure_json_mcp_server(path: Path, agent: str, *, verbose: bool) -> bool:
    if not path.parent.exists():
        return False
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
    else:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    servers = raw.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
    if _CYT_MCP_SERVER_KEY in servers:
        return False
    servers[_CYT_MCP_SERVER_KEY] = _canonical_cyt_mcp_entry(agent)
    raw["mcpServers"] = servers
    _atomic_write_text(path, json.dumps(raw, indent=2) + "\n")
    if verbose:
        print(f"cyt-client pairing: added {_CYT_MCP_SERVER_KEY} to {path}", flush=True)
    return True


def _ensure_codex_mcp_server(path: Path, agent: str, *, verbose: bool) -> bool:
    if not path.parent.exists():
        return False
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    marker = "[mcp_servers.cyt-mcp]"
    if marker in text or "cyt-mcp" in text:
        return False
    block = f'\n[mcp_servers.cyt-mcp]\ncommand = "cyt-mcp"\nargs = ["--agent", "{agent}"]\n'
    _atomic_write_text(path, text.rstrip() + block)
    if verbose:
        print(f"cyt-client pairing: added cyt-mcp to {path}", flush=True)
    return True


def _cursor_hooks_template(agent: str) -> dict[str, Any]:
    return {
        "version": 1,
        "hooks": {
            "sessionStart": [
                {
                    "command": f"CYT_LAUNCH_AGENT={agent} cyt hook daemon start --unattended",
                    "timeout": 60,
                },
                {"command": f"CYT_LAUNCH_AGENT={agent} cyt-client", "timeout": 60},
            ],
            "sessionEnd": [{"command": f"CYT_LAUNCH_AGENT={agent} cyt-client", "timeout": 60}],
            "beforeSubmitPrompt": [
                {"command": f"CYT_LAUNCH_AGENT={agent} cyt-client", "timeout": 60},
            ],
            "preToolUse": [{"command": f"CYT_LAUNCH_AGENT={agent} cyt-client", "timeout": 60}],
            "beforeMCPExecution": [
                {"command": f"CYT_LAUNCH_AGENT={agent} cyt-client", "timeout": 60},
            ],
        },
    }


def _merge_hook_commands(
    existing: dict[str, Any],
    required: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    changed = False
    hooks = existing.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
        changed = True
    req_hooks = required.get("hooks")
    if not isinstance(req_hooks, dict):
        return existing, changed
    for event, commands in req_hooks.items():
        if not isinstance(commands, list):
            continue
        current = hooks.get(event)
        if not isinstance(current, list):
            hooks[event] = list(commands)
            changed = True
            continue
        known = {json.dumps(item, sort_keys=True) for item in current if isinstance(item, dict)}
        for command in commands:
            if not isinstance(command, dict):
                continue
            key = json.dumps(command, sort_keys=True)
            if key not in known:
                current.append(command)
                changed = True
    if changed:
        existing["hooks"] = hooks
    return existing, changed


def _ensure_hooks_file(path: Path, agent: str, *, verbose: bool) -> bool:
    if not path.parent.exists():
        return False
    required = _cursor_hooks_template(agent)
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
    else:
        existing = {}
    if not isinstance(existing, dict):
        existing = {}
    merged, changed = _merge_hook_commands(existing, required)
    if not changed:
        return False
    _atomic_write_text(path, json.dumps(merged, indent=2) + "\n")
    if verbose:
        print(f"cyt-client pairing: updated hooks in {path}", flush=True)
    return True


def _session_id_from_payload(payload: dict[str, Any]) -> str:
    for layer in (
        payload,
        payload.get("payload") if isinstance(payload.get("payload"), dict) else {},
    ):
        if not isinstance(layer, dict):
            continue
        for key in ("session_id", "sessionId", "conversation_id"):
            raw = layer.get(key)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
    return ""


def repair_pairing(
    payload: dict[str, Any],
    *,
    verbose: bool = False,
    session_start: bool = True,
) -> None:
    if not tools_from_includes_cyt_mcp():
        return
    agent = (
        infer_harness_agent(payload) or os.environ.get("CYT_LAUNCH_AGENT", "").strip() or "cursor"
    )
    session_id = _session_id_from_payload(payload)
    if session_start and session_id:
        key = (agent, session_id)
        if key in _REPAIRED_SESSIONS:
            return
        _REPAIRED_SESSIONS.add(key)

    mcp_path = _AGENT_MCP_PATHS.get(agent)
    if mcp_path is not None:
        expanded = mcp_path.expanduser()
        if agent == "codex":
            _ensure_codex_mcp_server(expanded, agent, verbose=verbose)
        else:
            _ensure_json_mcp_server(expanded, agent, verbose=verbose)

    hooks_path = _AGENT_HOOK_PATHS.get(agent)
    if hooks_path is not None:
        _ensure_hooks_file(hooks_path.expanduser(), agent, verbose=verbose)

    _ = resolve_config_path()
