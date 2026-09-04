"""Bidirectional cyt-mcp / cyt-client config pairing (stdlib only, session lifecycle)."""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import cyt_client.config as cyt_client_config
from cyt_client.agent import infer_harness_agent
from cyt_client.config import resolve_config_path
from cyt_client.hook_invocation import (
    cursor_pairing_hooks,
    hooks_use_launch_agent_prefix,
    resolve_pairing_dev_context,
    runtime_dev_repo_from_client,
    runtime_dev_repo_from_mcp,
    strip_cyt_hook_entries,
)
from cyt_client.mcp_entry import (
    CYT_MCP_SERVER_KEY,
    CYT_MCP_WORKSPACE_SERVER_KEY,
    LEGACY_CYT_MCP_SERVER_KEY,
    LEGACY_CYT_MCP_WORKSPACE_SERVER_KEY,
    DEFAULT_AGGREGATOR_PATH,
    build_cyt_mcp_mcp_server_entry,
    codex_cyt_mcp_toml_block,
    load_aggregator_transport_settings,
    mcp_entries_equivalent,
)
from cyt_client.rules_file import workspace_root_from_payload
from cyt_client.skip import hook_skip_enabled

CURSOR_WORKSPACE_FOLDER = "${workspaceFolder}"

_WORKSPACE_AGENT_MCP_PATHS: dict[str, str] = {
    "cursor": ".cursor/mcp.json",
    "claude": ".mcp.json",
    "codex": ".codex/config.toml",
}

_AGENT_CYT_DIRS: dict[str, str] = {
    "cursor": ".cursor",
    "claude": ".claude",
    "codex": ".codex",
}

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


def _resolve_dev_context(
    agent: str,
    *,
    runtime_repo: Path | None = None,
) -> tuple[bool, Path | None]:
    hooks_path = _AGENT_HOOK_PATHS.get(agent)
    mcp_path = _AGENT_MCP_PATHS.get(agent)
    return resolve_pairing_dev_context(
        agent,
        hooks_path=hooks_path.expanduser() if hooks_path is not None else None,
        mcp_path=mcp_path.expanduser() if mcp_path is not None else None,
        runtime_repo=runtime_repo,
    )


def _canonical_cyt_mcp_entry(
    agent: str,
    *,
    runtime_repo: Path | None = None,
    aggregator_config: Path | str | None = None,
    workspace_cwd: str | None = None,
) -> dict[str, Any]:
    agg_path = aggregator_config if isinstance(aggregator_config, Path) else None
    transport, host, port, mcp_path, _catalog_path = load_aggregator_transport_settings(agg_path)
    use_dev, dev_repo_root = _resolve_dev_context(agent, runtime_repo=runtime_repo)
    dev_script_rel: str | None = None
    if use_dev and dev_repo_root is not None:
        dev_script_rel = "src/cyt_mcp/cli.py"
    return build_cyt_mcp_mcp_server_entry(
        agent,
        transport=transport,
        dev_repo_root=dev_repo_root,
        dev_script_rel=dev_script_rel,
        http_host=host,
        http_port=port,
        http_mcp_path=mcp_path,
        aggregator_config=aggregator_config,
        workspace_cwd=workspace_cwd,
    )


def _workspace_agent_mcp_path(workspace_root: Path, agent: str) -> Path:
    rel = _WORKSPACE_AGENT_MCP_PATHS.get(agent, ".cursor/mcp.json")
    return workspace_root / rel


def _resolve_workspace_server_defs_path(workspace_root: Path, agent: str) -> Path | None:
    from cyt_mcp.workspace_catalog import workspace_server_defs_path

    return workspace_server_defs_path(workspace_root, agent)


def _workspace_aggregator_config_ref(agent: str, workspace_root: Path) -> str:
    rel = ".agents/cyt/config/mcp-aggregator.yaml"
    if agent == "cursor":
        return f"{CURSOR_WORKSPACE_FOLDER}/{rel}"
    return str(workspace_root / rel)


def _ensure_json_mcp_server(
    path: Path,
    agent: str,
    *,
    verbose: bool,
    runtime_repo: Path | None = None,
    server_key: str = CYT_MCP_SERVER_KEY,
    aggregator_config: Path | str | None = None,
    workspace_cwd: str | None = None,
) -> bool:
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
    desired = _canonical_cyt_mcp_entry(
        agent,
        runtime_repo=runtime_repo,
        aggregator_config=aggregator_config,
        workspace_cwd=workspace_cwd,
    )
    existing = servers.get(server_key)
    if mcp_entries_equivalent(existing, desired):
        return False
    merged = dict(existing) if isinstance(existing, dict) else {}
    merged.update(desired)
    servers = dict(servers)
    if server_key == CYT_MCP_WORKSPACE_SERVER_KEY:
        servers.pop(LEGACY_CYT_MCP_WORKSPACE_SERVER_KEY, None)
    if server_key == CYT_MCP_SERVER_KEY:
        servers.pop(LEGACY_CYT_MCP_SERVER_KEY, None)
    servers[server_key] = merged
    raw["mcpServers"] = servers
    _atomic_write_text(path, json.dumps(raw, indent=2) + "\n")
    if verbose:
        print(f"cyt-client pairing: updated {server_key} in {path}", flush=True)
    return True


def _ensure_codex_mcp_server(
    path: Path,
    agent: str,
    *,
    verbose: bool,
    runtime_repo: Path | None = None,
    server_key: str = CYT_MCP_SERVER_KEY,
    aggregator_config: Path | str | None = None,
    workspace_cwd: str | None = None,
) -> bool:
    if not path.parent.exists():
        return False
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    marker = f"[mcp_servers.{server_key}]"
    desired = _canonical_cyt_mcp_entry(
        agent,
        runtime_repo=runtime_repo,
        aggregator_config=aggregator_config,
        workspace_cwd=workspace_cwd,
    )
    block = codex_cyt_mcp_toml_block(agent, desired, server_key=server_key)
    legacy_marker = f"[mcp_servers.{LEGACY_CYT_MCP_WORKSPACE_SERVER_KEY}]"
    if legacy_marker in text and server_key == CYT_MCP_WORKSPACE_SERVER_KEY:
        before, _, after = text.partition(legacy_marker)
        next_section = after.find("\n[mcp_servers.")
        if next_section >= 0:
            text = before.rstrip() + after[next_section:]
        else:
            text = before.rstrip() + "\n"
    legacy_user_marker = f"[mcp_servers.{LEGACY_CYT_MCP_SERVER_KEY}]"
    if legacy_user_marker in text and server_key == CYT_MCP_SERVER_KEY:
        before, _, after = text.partition(legacy_user_marker)
        next_section = after.find("\n[mcp_servers.")
        if next_section >= 0:
            text = before.rstrip() + after[next_section:]
        else:
            text = before.rstrip() + "\n"
    if marker in text:
        before, _, after = text.partition(marker)
        next_section = after.find("\n[mcp_servers.")
        if next_section >= 0:
            text = before.rstrip() + after[next_section:]
        else:
            text = before.rstrip() + "\n"
    elif server_key in text and block.strip() in text:
        return False
    _atomic_write_text(path, text.rstrip() + block)
    if verbose:
        print(f"cyt-client pairing: added {server_key} to {path}", flush=True)
    return True


_LEGACY_CURSOR_TOOL_HOOK_EVENTS = ("beforeMCPExecution", "afterMCPExecution")


def _strip_legacy_cursor_tool_hook_events(
    merged_hooks: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    changed = False
    for event_name in _LEGACY_CURSOR_TOOL_HOOK_EVENTS:
        current = merged_hooks.get(event_name)
        if not isinstance(current, list):
            continue
        stripped = strip_cyt_hook_entries(current)
        if stripped == current:
            continue
        if stripped:
            merged_hooks[event_name] = stripped
        else:
            del merged_hooks[event_name]
        changed = True
    return merged_hooks, changed


def _upsert_pairing_hooks(
    existing: dict[str, Any],
    required_events: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, Any], bool]:
    hooks = existing.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
    changed = False
    merged_hooks = dict(hooks)
    for event_name, required_entries in required_events.items():
        current = merged_hooks.get(event_name)
        if not isinstance(current, list):
            current = []
        stripped = strip_cyt_hook_entries(current)
        next_entries = stripped + [dict(entry) for entry in required_entries]
        if next_entries != current:
            merged_hooks[event_name] = next_entries
            changed = True
    merged_hooks, legacy_changed = _strip_legacy_cursor_tool_hook_events(merged_hooks)
    changed = changed or legacy_changed
    if changed:
        existing["hooks"] = merged_hooks
        if "version" not in existing:
            existing["version"] = 1
    return existing, changed


def _ensure_hooks_file(
    path: Path,
    agent: str,
    *,
    verbose: bool,
    runtime_repo: Path | None = None,
) -> bool:
    if not path.parent.exists():
        return False
    use_dev, dev_repo_root = _resolve_dev_context(agent, runtime_repo=runtime_repo)
    set_launch_agent = hooks_use_launch_agent_prefix(path)
    required_events = cursor_pairing_hooks(
        agent,
        use_dev=use_dev,
        dev_repo_root=dev_repo_root,
        set_launch_agent=set_launch_agent,
    )
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
    else:
        existing = {}
    if not isinstance(existing, dict):
        existing = {}
    merged, changed = _upsert_pairing_hooks(existing, required_events)
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


def _repair_global_mcp_pairing(
    agent: str,
    *,
    verbose: bool,
    runtime_repo: Path | None,
) -> None:
    mcp_path = _AGENT_MCP_PATHS.get(agent)
    if mcp_path is None:
        return
    expanded = mcp_path.expanduser()
    global_agg = DEFAULT_AGGREGATOR_PATH.expanduser()
    if agent == "codex":
        _ensure_codex_mcp_server(
            expanded,
            agent,
            verbose=verbose,
            runtime_repo=runtime_repo,
            aggregator_config=global_agg,
        )
        return
    _ensure_json_mcp_server(
        expanded,
        agent,
        verbose=verbose,
        runtime_repo=runtime_repo,
        aggregator_config=global_agg,
    )


def _repair_workspace_mcp_pairing(
    agent: str,
    workspace_root: Path,
    *,
    verbose: bool,
    runtime_repo: Path | None,
) -> None:
    defs_path = _resolve_workspace_server_defs_path(workspace_root, agent)
    if defs_path is None:
        return
    ws_mcp = _workspace_agent_mcp_path(workspace_root, agent)
    agg_ref = _workspace_aggregator_config_ref(agent, workspace_root)
    workspace_cwd = CURSOR_WORKSPACE_FOLDER if agent == "cursor" else None
    if agent == "codex":
        _ensure_codex_mcp_server(
            ws_mcp,
            agent,
            verbose=verbose,
            runtime_repo=runtime_repo,
            server_key=CYT_MCP_WORKSPACE_SERVER_KEY,
            aggregator_config=agg_ref,
            workspace_cwd=workspace_cwd,
        )
        return
    _ensure_json_mcp_server(
        ws_mcp,
        agent,
        verbose=verbose,
        runtime_repo=runtime_repo,
        server_key=CYT_MCP_WORKSPACE_SERVER_KEY,
        aggregator_config=agg_ref,
        workspace_cwd=workspace_cwd,
    )


def repair_pairing(
    payload: dict[str, Any],
    *,
    verbose: bool = False,
    session_start: bool = True,
    runtime_repo: Path | None = None,
) -> None:
    if hook_skip_enabled(payload):
        if verbose:
            print("cyt-client: skip.txt present; pairing disabled", file=sys.stderr)
        return
    if not cyt_client_config.tools_from_includes_cyt_mcp():
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

    resolved_runtime = runtime_repo or runtime_dev_repo_from_client()

    _repair_global_mcp_pairing(agent, verbose=verbose, runtime_repo=resolved_runtime)

    workspace_root = workspace_root_from_payload(payload)
    if workspace_root is not None:
        _repair_workspace_mcp_pairing(
            agent,
            workspace_root,
            verbose=verbose,
            runtime_repo=resolved_runtime,
        )

    # Cursor/Claude/Codex hooks.json is updated only via ``cyt hook <agent>`` (never here).

    _ = resolve_config_path()


def repair_pairing_from_mcp_runtime(*, agent: str | None = None, verbose: bool = False) -> None:
    """Repair MCP pairing when cyt-mcp starts (dev or prod runtime)."""
    resolved_agent = (agent or "cursor").strip() or "cursor"
    runtime_repo = runtime_dev_repo_from_mcp()
    repair_pairing(
        {
            "hook_event_name": "sessionStart",
            "session_id": "cyt-mcp-startup",
            "cyt_agent": resolved_agent,
            "cwd": str(Path.cwd()),
        },
        verbose=verbose,
        session_start=False,
        runtime_repo=runtime_repo,
    )
