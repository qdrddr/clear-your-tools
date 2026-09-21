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
    CYT_MCP_FRONTEND_SERVER_KEYS,
    CYT_MCP_USER_SERVER_KEY,
    CYT_MCP_WORKSPACE_SERVER_KEY,
    LEGACY_CYT_MCP_SERVER_KEY,
    LEGACY_CYT_MCP_WORKSPACE_SERVER_KEY,
    build_cyt_mcp_mcp_server_entry,
    codex_cyt_mcp_toml_block,
    load_aggregator_transport_settings,
    mcp_entries_equivalent,
    user_aggregator_config_ref,
    workspace_aggregator_config_ref,
)
from cyt_client.rules_file import workspace_root_from_payload
from cyt_client.skip import hook_skip_enabled

_WORKSPACE_AGENT_MCP_PATHS: dict[str, str] = {
    "cursor": ".cursor/mcp.json",
    "claude": ".mcp.json",
    "codex": ".codex/config.toml",
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
    )


def _workspace_agent_mcp_path(workspace_root: Path, agent: str) -> Path:
    rel = _WORKSPACE_AGENT_MCP_PATHS.get(agent, ".cursor/mcp.json")
    return workspace_root / rel


def _resolve_workspace_server_defs_path(workspace_root: Path, agent: str) -> Path | None:
    from cyt_mcp.workspace_catalog import workspace_server_defs_path

    return workspace_server_defs_path(workspace_root, agent)


def _strip_frontend_keys_from_json_mcp(path: Path, *, verbose: bool) -> bool:
    if not path.is_file():
        return False
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(raw, dict):
        return False
    servers = raw.get("mcpServers")
    if not isinstance(servers, dict):
        return False
    removed = [key for key in servers if key in CYT_MCP_FRONTEND_SERVER_KEYS]
    if not removed:
        return False
    raw["mcpServers"] = {
        key: spec for key, spec in servers.items() if key not in CYT_MCP_FRONTEND_SERVER_KEYS
    }
    _atomic_write_text(path, json.dumps(raw, indent=2) + "\n")
    if verbose:
        print(
            f"cyt-client pairing: removed {', '.join(removed)} from {path}",
            flush=True,
        )
    return True


def _strip_frontend_keys_from_codex_mcp(path: Path, *, verbose: bool) -> bool:
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    changed = False
    for key in CYT_MCP_FRONTEND_SERVER_KEYS:
        marker = f"[mcp_servers.{key}]"
        if marker not in text:
            continue
        before, _, after = text.partition(marker)
        next_section = after.find("\n[mcp_servers.")
        if next_section >= 0:
            text = before.rstrip() + after[next_section:]
        else:
            text = before.rstrip() + "\n"
        changed = True
    if not changed:
        return False
    _atomic_write_text(path, text)
    if verbose:
        print(f"cyt-client pairing: removed cyt-mcp frontend from {path}", flush=True)
    return True


def _ensure_json_mcp_server(
    path: Path,
    agent: str,
    *,
    verbose: bool,
    runtime_repo: Path | None = None,
    server_key: str = CYT_MCP_USER_SERVER_KEY,
    aggregator_config: Path | str | None = None,
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
    )
    existing = servers.get(server_key)
    if mcp_entries_equivalent(existing, desired):
        return False
    merged = dict(existing) if isinstance(existing, dict) else {}
    merged.update(desired)
    servers = {
        key: spec
        for key, spec in servers.items()
        if key not in CYT_MCP_FRONTEND_SERVER_KEYS or key == server_key
    }
    for legacy_key in CYT_MCP_FRONTEND_SERVER_KEYS:
        if legacy_key != server_key:
            servers.pop(legacy_key, None)
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
    server_key: str = CYT_MCP_USER_SERVER_KEY,
    aggregator_config: Path | str | None = None,
) -> bool:
    if not path.parent.exists():
        return False
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    marker = f"[mcp_servers.{server_key}]"
    desired = _canonical_cyt_mcp_entry(
        agent,
        runtime_repo=runtime_repo,
        aggregator_config=aggregator_config,
    )
    block = codex_cyt_mcp_toml_block(agent, desired, server_key=server_key)
    for legacy_key in CYT_MCP_FRONTEND_SERVER_KEYS:
        legacy_marker = f"[mcp_servers.{legacy_key}]"
        if legacy_marker in text and legacy_key != server_key:
            before, _, after = text.partition(legacy_marker)
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


def _repair_user_mcp_pairing(
    agent: str,
    workspace_root: Path | None,
    *,
    verbose: bool,
    runtime_repo: Path | None,
) -> None:
    from cyt.hook.install_scope import CytInstallScope
    from cyt.tools.cyt_mcp_setup import has_migratable_mcp_backends

    use_dev, _ = _resolve_dev_context(agent, runtime_repo=runtime_repo)
    scope = CytInstallScope(workspace_root=workspace_root)
    if not use_dev and not has_migratable_mcp_backends(agent, scope):
        return

    mcp_path = _AGENT_MCP_PATHS.get(agent)
    if mcp_path is None:
        return
    expanded = mcp_path.expanduser()
    agg_ref = user_aggregator_config_ref()
    if agent == "codex":
        _ensure_codex_mcp_server(
            expanded,
            agent,
            verbose=verbose,
            runtime_repo=runtime_repo,
            server_key=CYT_MCP_USER_SERVER_KEY,
            aggregator_config=agg_ref,
        )
        return
    _ensure_json_mcp_server(
        expanded,
        agent,
        verbose=verbose,
        runtime_repo=runtime_repo,
        server_key=CYT_MCP_USER_SERVER_KEY,
        aggregator_config=agg_ref,
    )


def _repair_cyt_mcp_dev_wrapper_command(
    mcp_path: Path,
    server_key: str,
    agent: str,
    *,
    runtime_repo: Path | None = None,
    verbose: bool = False,
) -> bool:
    """Upgrade legacy ``cyt-mcp-dev.cmd`` to ``hooks/cyt/mcp-dev.cmd`` without full reinstall."""
    if not mcp_path.is_file():
        return False
    try:
        raw = json.loads(mcp_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return False
    if not isinstance(raw, dict):
        return False
    servers = raw.get("mcpServers")
    if not isinstance(servers, dict):
        return False
    existing = servers.get(server_key)
    if not isinstance(existing, dict):
        return False
    command = existing.get("command")
    if not isinstance(command, str) or not command.strip():
        return False
    from cyt_client.hook_invocation import (
        LEGACY_WINDOWS_CYT_MCP_DEV_WRAPPER,
        cyt_mcp_dev_wrapper_path,
        install_windows_cyt_mcp_dev_wrapper,
        is_cyt_mcp_dev_wrapper_command,
    )

    normalized = command.strip().casefold().replace("\\", "/")
    wrapper_path = cyt_mcp_dev_wrapper_path(agent)
    if normalized.endswith(LEGACY_WINDOWS_CYT_MCP_DEV_WRAPPER.casefold()):
        needs_repair = True
    elif is_cyt_mcp_dev_wrapper_command(command):
        expanded = Path(command).expanduser()
        needs_repair = not expanded.is_file()
    else:
        return False
    if not needs_repair:
        return False
    use_dev, dev_repo_root = _resolve_dev_context(agent, runtime_repo=runtime_repo)
    if not use_dev or dev_repo_root is None:
        return False
    wrapper_path = install_windows_cyt_mcp_dev_wrapper(
        dev_repo_root=dev_repo_root,
        agent=agent,
    )
    if command == str(wrapper_path):
        return False
    merged = dict(existing)
    merged["command"] = str(wrapper_path)
    servers = dict(servers)
    servers[server_key] = merged
    raw["mcpServers"] = servers
    _atomic_write_text(mcp_path, json.dumps(raw, indent=2) + "\n")
    if verbose:
        print(
            f"cyt-client pairing: upgraded {server_key} dev wrapper command in {mcp_path}",
            flush=True,
        )
    return True


def _workspace_cyt_mcp_install_complete(agent: str, workspace_root: Path) -> bool:
    """Return True when project cyt-mcp-ws install artifacts are already present."""
    from cyt.hook.install_scope import CytInstallScope

    scope = CytInstallScope(workspace_root=workspace_root)
    if not scope.has_workspace:
        return False
    agg_path = scope.workspace_aggregator_path(agent)
    defs_path = scope.workspace_server_defs_path(agent)
    mcp_path = scope.workspace_agent_mcp_path(agent)
    if agg_path is None or defs_path is None or mcp_path is None:
        return False
    if not (agg_path.is_file() and defs_path.is_file() and mcp_path.is_file()):
        return False
    try:
        raw = json.loads(mcp_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return False
    if not isinstance(raw, dict):
        return False
    servers = raw.get("mcpServers")
    if not isinstance(servers, dict):
        return False
    return CYT_MCP_WORKSPACE_SERVER_KEY in servers


def _workspace_mcp_collides_with_user_global(agent: str, workspace_root: Path) -> bool:
    user_mcp = _AGENT_MCP_PATHS.get(agent)
    if user_mcp is None:
        return False
    ws_mcp = _workspace_agent_mcp_path(workspace_root, agent)
    try:
        return ws_mcp.resolve() == user_mcp.expanduser().resolve()
    except OSError:
        return False


def _repair_workspace_mcp_pairing(
    agent: str,
    workspace_root: Path,
    *,
    verbose: bool,
) -> None:
    from cyt.hook.install_scope import CytInstallScope
    from cyt.tools.cyt_mcp_setup import setup_cyt_mcp_workspace_for_agent

    ws_mcp = _workspace_agent_mcp_path(workspace_root, agent)
    if _workspace_mcp_collides_with_user_global(agent, workspace_root):
        if verbose:
            print(
                "cyt-client pairing: skipping workspace MCP repair "
                f"(workspace path equals user global MCP: {ws_mcp})",
                flush=True,
            )
        return

    from cyt_mcp.debug_session_log import debug_session_log

    debug_session_log(
        hypothesis_id="B",
        location="pairing.py:_repair_workspace_mcp_pairing",
        message="workspace MCP pairing repair with migrate_backends=False",
        data={"agent": agent, "workspace_root": str(workspace_root)},
    )
    scope = CytInstallScope(workspace_root=workspace_root)
    setup_cyt_mcp_workspace_for_agent(
        agent,
        scope=scope,
        migrate_backends=False,
    )


def repair_pairing(
    payload: dict[str, Any],
    *,
    verbose: bool = False,
    session_start: bool = True,
    runtime_repo: Path | None = None,
    repair_user: bool = True,
    repair_workspace: bool = True,
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
    workspace_root = workspace_root_from_payload(payload)

    if repair_user:
        _repair_user_mcp_pairing(
            agent,
            workspace_root,
            verbose=verbose,
            runtime_repo=resolved_runtime,
        )

    if repair_workspace and workspace_root is not None:
        _repair_workspace_mcp_pairing(
            agent,
            workspace_root,
            verbose=verbose,
        )

    _ = resolve_config_path()


def repair_pairing_from_mcp_runtime(
    *,
    agent: str | None = None,
    catalog_scope: str = "user",
    verbose: bool = False,
) -> None:
    """Repair MCP pairing when cyt-mcp starts (dev or prod runtime)."""
    from cyt_mcp.debug_session_log import debug_session_log

    resolved_agent = (agent or "cursor").strip() or "cursor"
    resolved_scope = (catalog_scope or "user").strip() or "user"
    runtime_repo = runtime_dev_repo_from_mcp()
    debug_session_log(
        hypothesis_id="B",
        location="pairing.py:repair_pairing_from_mcp_runtime",
        message="MCP runtime pairing repair starting",
        data={
            "agent": resolved_agent,
            "catalog_scope": resolved_scope,
            "cwd": str(Path.cwd()),
            "runtime_repo": str(runtime_repo) if runtime_repo is not None else None,
        },
    )
    repair_workspace = resolved_scope == "workspace"
    repair_user = resolved_scope == "user"
    workspace_root = Path.cwd()
    if repair_workspace:
        ws_mcp = _workspace_agent_mcp_path(workspace_root, resolved_agent)
        _repair_cyt_mcp_dev_wrapper_command(
            ws_mcp,
            CYT_MCP_WORKSPACE_SERVER_KEY,
            resolved_agent,
            runtime_repo=runtime_repo,
            verbose=verbose,
        )
    if repair_user:
        user_mcp = _AGENT_MCP_PATHS.get(resolved_agent)
        if user_mcp is not None:
            _repair_cyt_mcp_dev_wrapper_command(
                user_mcp.expanduser(),
                CYT_MCP_USER_SERVER_KEY,
                resolved_agent,
                runtime_repo=runtime_repo,
                verbose=verbose,
            )
    if repair_workspace and _workspace_cyt_mcp_install_complete(resolved_agent, workspace_root):
        debug_session_log(
            hypothesis_id="B",
            location="pairing.py:repair_pairing_from_mcp_runtime",
            message="skipping workspace pairing repair; cyt-mcp-ws already installed",
            data={
                "agent": resolved_agent,
                "catalog_scope": resolved_scope,
                "workspace_root": str(workspace_root),
            },
        )
        return
    if not repair_workspace:
        debug_session_log(
            hypothesis_id="B",
            location="pairing.py:repair_pairing_from_mcp_runtime",
            message="skipping workspace pairing repair for user-scoped cyt-mcp",
            data={"agent": resolved_agent, "catalog_scope": resolved_scope},
        )
    repair_pairing(
        {
            "hook_event_name": "sessionStart",
            "session_id": "cyt-mcp-startup",
            "cyt_agent": resolved_agent,
            "cwd": str(workspace_root),
        },
        verbose=verbose,
        session_start=False,
        runtime_repo=runtime_repo,
        repair_user=repair_user,
        repair_workspace=repair_workspace,
    )
