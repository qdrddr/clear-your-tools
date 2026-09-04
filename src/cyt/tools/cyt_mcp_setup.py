"""Wizard helpers for cyt-mcp aggregator setup and agent MCP migration."""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from typing import Any

from cyt.hook.cli_invocation import (
    HookCliInvocation,
    cyt_mcp_cli_script_relpath,
    detect_hook_cli_invocation,
)
from cyt.hook.install_scope import (
    GLOBAL_AGENT_MCP_PATHS,
    WORKSPACE_ALL_AGENTS_CYT_DIR,
    WORKSPACE_CYT_CONFIG_SUBDIR,
    CytInstallScope,
)
from cyt.permissions.paths import PermissionScope
from cyt.proxy.setup_wizard import _prompt, _prompt_yes_no
from cyt_client.mcp_entry import (
    CYT_MCP_SERVER_KEY,
    CYT_MCP_WORKSPACE_SERVER_KEY,
    LEGACY_CYT_MCP_SERVER_KEY,
    LEGACY_CYT_MCP_WORKSPACE_SERVER_KEY,
    CytMcpTransport,
    backend_mcp_servers,
    build_cyt_mcp_mcp_server_entry,
    codex_cyt_mcp_toml_block,
    load_aggregator_transport_settings,
    normalize_cyt_mcp_transport,
)

DEFAULT_AGGREGATOR_PATH = Path("~/.config/cyt/mcp-aggregator.yaml")
DEFAULT_MCP_DIR = Path("~/.config/cyt/mcp")
DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8765
DEFAULT_WORKSPACE_HTTP_PORT = 8766
DEFAULT_MCP_PATH = "/mcp"
DEFAULT_CATALOG_PATH = "/catalog"

_AGENT_SOURCE_PATHS: dict[str, Path] = GLOBAL_AGENT_MCP_PATHS

CURSOR_WORKSPACE_FOLDER = "${workspaceFolder}"


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


def migrate_agent_backends_from(
    source_path: Path,
    target_path: Path,
    *,
    agent: str = "cursor",
    permission_scope: PermissionScope = "global",
    workspace_root: Path | None = None,
) -> Path:
    """Copy backend MCP servers from *source_path* into *target_path*."""
    from cyt.permissions.mcp_defs import disabled_server_names, import_disabled_servers_to_deny

    servers = backend_mcp_servers(_extract_mcp_servers_from_json(source_path))
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if not servers:
        if not target_path.is_file():
            _atomic_write_text(target_path, json.dumps({"mcpServers": {}}, indent=2) + "\n")
        return target_path
    payload = {"mcpServers": servers}
    _atomic_write_text(target_path, json.dumps(payload, indent=2) + "\n")
    print(f"Migrated backend MCP servers to {target_path}", file=sys.stderr)

    disabled = disabled_server_names(servers)
    if disabled:
        config_path = import_disabled_servers_to_deny(
            disabled,
            scope="workspace" if permission_scope == "workspace" else "global",
            agent=agent,
            workspace_root=workspace_root,
        )
        if config_path is not None:
            print(
                f"Synced disabled MCP servers to permissions deny in {config_path}: "
                + ", ".join(disabled),
                file=sys.stderr,
            )
    return target_path


def migrate_agent_backends(agent: str) -> Path:
    """Copy existing Global User agent MCP servers into ~/.config/cyt/mcp/<agent>.json."""
    agent = agent.strip() or "cursor"
    scope = CytInstallScope.from_cwd()
    target = DEFAULT_MCP_DIR.expanduser() / f"{agent}.json"
    source_path = scope.global_agent_mcp_path(agent)
    return migrate_agent_backends_from(
        source_path,
        target,
        agent=agent,
        permission_scope="global",
    )


def write_mcp_aggregator_yaml(
    agent: str,
    *,
    backends_path: Path | None = None,
    transport: CytMcpTransport = "stdio",
    verify_only: bool = False,
    aggregator_path: Path | None = None,
    http_port: int | None = None,
) -> Path:
    return write_mcp_aggregator_yaml_at(
        aggregator_path or DEFAULT_AGGREGATOR_PATH.expanduser(),
        agent,
        backends_path=backends_path,
        transport=transport,
        verify_only=verify_only,
        http_port=http_port or DEFAULT_HTTP_PORT,
    )


def _workspace_agent_mcp_yaml_ref(aggregator_path: Path, backends_path: Path) -> str:
    """Return a portable agents.* path relative to the workspace aggregator directory."""
    agg_dir = aggregator_path.expanduser().resolve().parent
    backend_resolved = backends_path.expanduser().resolve()
    try:
        return backend_resolved.relative_to(agg_dir).as_posix()
    except ValueError:
        return backend_resolved.as_posix()


def write_mcp_aggregator_yaml_at(
    path: Path,
    agent: str,
    *,
    backends_path: Path | None = None,
    transport: CytMcpTransport = "stdio",
    verify_only: bool = False,
    http_port: int = DEFAULT_HTTP_PORT,
    workspace_scoped: bool = False,
) -> Path:
    agent = agent.strip() or "cursor"
    backends = backends_path or (DEFAULT_MCP_DIR.expanduser() / f"{agent}.json")
    lines = [
        f"default_agent: {agent}",
        "agents:",
    ]
    if workspace_scoped:
        backends_ref = _workspace_agent_mcp_yaml_ref(path, backends)
        lines.append(f"  {agent}: {backends_ref}")
        lines.append("catalog_scope: workspace")
    else:
        mcp_dir = DEFAULT_MCP_DIR.expanduser()
        lines.extend(
            [
                f"  cursor: {mcp_dir / 'cursor.json'}",
                f"  claude: {mcp_dir / 'claude.json'}",
                f"  codex: {mcp_dir / 'codex.json'}",
            ],
        )
    lines.extend(
        [
            f"transport: {transport}",
            f"verify_only: {'true' if verify_only else 'false'}",
            "http:",
            f"  host: {DEFAULT_HTTP_HOST}",
            f"  port: {http_port}",
            f"  mcp_path: {DEFAULT_MCP_PATH}",
            f"  catalog_path: {DEFAULT_CATALOG_PATH}",
            "codex_stubs_include_description: true",
            "",
        ],
    )
    _atomic_write_text(path, "\n".join(lines))
    print(f"\nWrote {path} (agent mapping includes {backends})", file=sys.stderr)
    return path


def cyt_mcp_hook_settings_overlay(
    *,
    transport: CytMcpTransport,
    agent: str,
) -> dict[str, Any]:
    del transport
    return {"agent": agent.strip() or "cursor"}


def _build_cyt_mcp_entry(
    agent: str,
    *,
    invocation: HookCliInvocation | None,
    transport: CytMcpTransport,
    aggregator_config: Path | str | None,
    workspace_cwd: str | None = None,
) -> dict[str, Any]:
    invocation = invocation or detect_hook_cli_invocation()
    agg_path = aggregator_config if isinstance(aggregator_config, Path) else None
    _, host, port, mcp_path, _catalog_path = load_aggregator_transport_settings(agg_path)
    if invocation.is_dev and invocation.repo_root is not None:
        return build_cyt_mcp_mcp_server_entry(
            agent,
            transport=transport,
            dev_repo_root=invocation.repo_root,
            dev_script_rel=cyt_mcp_cli_script_relpath(),
            http_host=host,
            http_port=port,
            http_mcp_path=mcp_path,
            aggregator_config=aggregator_config,
            workspace_cwd=workspace_cwd,
        )
    return build_cyt_mcp_mcp_server_entry(
        agent,
        transport=transport,
        http_host=host,
        http_port=port,
        http_mcp_path=mcp_path,
        aggregator_config=aggregator_config,
        workspace_cwd=workspace_cwd,
    )


def _write_codex_cyt_mcp_entry(
    path: Path,
    agent: str,
    entry: dict[str, Any],
    *,
    server_key: str = CYT_MCP_SERVER_KEY,
) -> None:
    if path.is_file():
        text = path.read_text(encoding="utf-8")
    else:
        text = ""
    marker = f"[mcp_servers.{server_key}]"
    block = codex_cyt_mcp_toml_block(agent, entry, server_key=server_key)
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
        return
    _atomic_write_text(path, text.rstrip() + block)
    print(f"Wrote {server_key} entry to {path}", file=sys.stderr)


def _write_json_cyt_mcp_entry(
    path: Path,
    entry: dict[str, Any],
    *,
    agent: str,
    transport: CytMcpTransport,
    server_key: str = CYT_MCP_SERVER_KEY,
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
        servers: dict[str, Any] = {server_key: entry}
    else:
        existing = raw.get("mcpServers")
        if not isinstance(existing, dict):
            existing = {}
        servers = dict(existing)
        if server_key == CYT_MCP_WORKSPACE_SERVER_KEY:
            servers.pop(LEGACY_CYT_MCP_WORKSPACE_SERVER_KEY, None)
        if server_key == CYT_MCP_SERVER_KEY:
            servers.pop(LEGACY_CYT_MCP_SERVER_KEY, None)
        servers[server_key] = entry
    raw["mcpServers"] = servers
    _atomic_write_text(path, json.dumps(raw, indent=2) + "\n")
    print(f"Wrote {server_key} entry to {path}", file=sys.stderr)
    if transport == "http":
        print(
            "Start cyt-mcp in HTTP mode separately, for example: "
            "cyt-mcp --agent "
            f"{agent} --transport http",
            file=sys.stderr,
        )


def write_agent_cyt_mcp_entry_at(
    path: Path,
    agent: str,
    *,
    invocation: HookCliInvocation | None = None,
    transport: CytMcpTransport = "stdio",
    server_key: str = CYT_MCP_SERVER_KEY,
    aggregator_config: Path | str | None = None,
    workspace_cwd: str | None = None,
    frontend_only: bool = False,
) -> None:
    agent = agent.strip() or "cursor"
    entry = _build_cyt_mcp_entry(
        agent,
        invocation=invocation,
        transport=transport,
        aggregator_config=aggregator_config,
        workspace_cwd=workspace_cwd,
    )
    if agent == "codex":
        _write_codex_cyt_mcp_entry(path, agent, entry, server_key=server_key)
        return
    _write_json_cyt_mcp_entry(
        path,
        entry,
        agent=agent,
        transport=transport,
        server_key=server_key,
        frontend_only=frontend_only,
    )


def write_agent_cyt_mcp_entry(
    agent: str,
    *,
    invocation: HookCliInvocation | None = None,
    transport: CytMcpTransport = "stdio",
    frontend_only: bool = False,
) -> None:
    scope = CytInstallScope.from_cwd()
    write_agent_cyt_mcp_entry_at(
        scope.global_agent_mcp_path(agent),
        agent,
        invocation=invocation,
        transport=transport,
        server_key=CYT_MCP_SERVER_KEY,
        aggregator_config=scope.global_aggregator_path(),
        frontend_only=frontend_only,
    )


def _workspace_aggregator_config_path(scope: CytInstallScope, agent: str) -> str:
    agg = scope.workspace_aggregator_path(agent)
    assert agg is not None
    rel = f"{WORKSPACE_ALL_AGENTS_CYT_DIR}/{WORKSPACE_CYT_CONFIG_SUBDIR}/mcp-aggregator.yaml"
    if agent == "cursor":
        return f"{CURSOR_WORKSPACE_FOLDER}/{rel}"
    return str(agg)


def _ensure_shared_workspace_config(
    shared_config: Path,
    *,
    legacy_agent_config: Path,
    legacy_root_config: Path,
) -> None:
    shared_config.parent.mkdir(parents=True, exist_ok=True)
    if shared_config.is_file():
        return
    for legacy in (legacy_agent_config, legacy_root_config):
        if legacy.is_file():
            legacy.rename(shared_config)
            return
    _atomic_write_text(shared_config, "{}\n")


def _migrate_workspace_legacy_files(
    cyt_dir: Path,
    config_dir: Path,
    agent: str,
    *,
    shared_config: Path | None,
) -> None:
    if shared_config is not None:
        _ensure_shared_workspace_config(
            shared_config,
            legacy_agent_config=config_dir / "config.yaml",
            legacy_root_config=cyt_dir / "config.yaml",
        )


def setup_cyt_mcp_workspace_for_agent(
    agent: str,
    scope: CytInstallScope,
    *,
    invocation: HookCliInvocation | None = None,
    transport: CytMcpTransport = "stdio",
    migrate_backends: bool = True,
    verify_only: bool = False,
) -> None:
    if not scope.has_workspace:
        return
    agent = agent.strip() or "cursor"
    resolved = invocation or detect_hook_cli_invocation()

    cyt_dir = scope.workspace_cyt_dir(agent)
    mcp_path = scope.workspace_agent_mcp_path(agent)
    defs_path = scope.workspace_server_defs_path(agent)
    agg_path = scope.workspace_aggregator_path(agent)
    if cyt_dir is None or mcp_path is None or defs_path is None or agg_path is None:
        return

    from cyt.migrations.workspace_paths import (
        ensure_canonical_workspace_aggregator,
        ensure_canonical_workspace_config,
        ensure_canonical_workspace_server_defs,
    )

    ensure_canonical_workspace_config(scope)
    ensure_canonical_workspace_aggregator(scope)
    ensure_canonical_workspace_server_defs(scope, agent)

    _migrate_workspace_legacy_files(
        cyt_dir,
        cyt_dir / "config",
        agent,
        shared_config=scope.workspace_all_agents_cyt_config_path(),
    )

    if migrate_backends:
        migrate_agent_backends_from(
            mcp_path,
            defs_path,
            agent=agent,
            permission_scope="workspace",
            workspace_root=scope.workspace_root,
        )
        write_mcp_aggregator_yaml_at(
            agg_path,
            agent,
            backends_path=defs_path,
            transport=transport,
            verify_only=verify_only,
            http_port=DEFAULT_WORKSPACE_HTTP_PORT,
            workspace_scoped=True,
        )
    elif verify_only:
        write_mcp_aggregator_yaml_at(
            agg_path,
            agent,
            backends_path=defs_path,
            transport=transport,
            verify_only=verify_only,
            http_port=DEFAULT_WORKSPACE_HTTP_PORT,
            workspace_scoped=True,
        )

    aggregator_arg = _workspace_aggregator_config_path(scope, agent)
    write_agent_cyt_mcp_entry_at(
        mcp_path,
        agent,
        invocation=resolved,
        transport=transport,
        server_key=CYT_MCP_WORKSPACE_SERVER_KEY,
        aggregator_config=aggregator_arg,
        workspace_cwd=CURSOR_WORKSPACE_FOLDER if agent == "cursor" else None,
        frontend_only=migrate_backends,
    )


def setup_cyt_mcp_for_agent(
    agent: str,
    *,
    invocation: HookCliInvocation | None = None,
    transport: CytMcpTransport = "stdio",
    migrate_backends: bool = True,
    verify_only: bool = False,
    scope: CytInstallScope | None = None,
    configure_workspace: bool | None = None,
) -> None:
    resolved = invocation or detect_hook_cli_invocation()
    install_scope = scope or CytInstallScope.from_cwd()

    if migrate_backends:
        backends = migrate_agent_backends(agent)
        write_mcp_aggregator_yaml(
            agent,
            backends_path=backends,
            transport=transport,
            verify_only=verify_only,
        )
    elif verify_only:
        write_mcp_aggregator_yaml(agent, transport=transport, verify_only=verify_only)
    write_agent_cyt_mcp_entry(
        agent,
        invocation=resolved,
        transport=transport,
        frontend_only=migrate_backends,
    )

    if not install_scope.has_workspace:
        return

    if configure_workspace is None:
        if sys.stdin.isatty():
            configure_workspace = _prompt_yes_no(
                "\nConfigure workspace-scoped cyt-mcp for this project?",
                default_yes=True,
            )
        else:
            configure_workspace = False

    if configure_workspace:
        setup_cyt_mcp_workspace_for_agent(
            agent,
            install_scope,
            invocation=resolved,
            transport=transport,
            migrate_backends=migrate_backends,
            verify_only=verify_only,
        )


def remove_workspace_cyt_mcp_for_agent(agent: str, scope: CytInstallScope) -> bool:
    """Remove workspace cyt-mcp artifacts; return True when anything changed."""
    if not scope.has_workspace:
        return False
    changed = False
    agent = agent.strip() or "cursor"

    mcp_path = scope.workspace_agent_mcp_path(agent)
    if mcp_path is not None and mcp_path.is_file():
        try:
            raw = json.loads(mcp_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        if isinstance(raw, dict):
            servers = raw.get("mcpServers")
            if isinstance(servers, dict):
                servers = dict(servers)
                removed_keys = [
                    key
                    for key in (
                        CYT_MCP_WORKSPACE_SERVER_KEY,
                        LEGACY_CYT_MCP_WORKSPACE_SERVER_KEY,
                    )
                    if key in servers
                ]
                if removed_keys:
                    for key in removed_keys:
                        del servers[key]
                    raw["mcpServers"] = servers
                    _atomic_write_text(mcp_path, json.dumps(raw, indent=2) + "\n")
                    changed = True
                    print(
                        f"Removed {', '.join(removed_keys)} from {mcp_path}",
                        file=sys.stderr,
                    )

    defs_path = scope.workspace_all_agents_cyt_mcp_defs_path(agent)
    if defs_path is not None and defs_path.is_file():
        defs_path.unlink()
        changed = True
        print(f"Removed workspace MCP server defs {defs_path}", file=sys.stderr)

    cyt_dir = scope.workspace_cyt_dir(agent)
    if cyt_dir is not None and cyt_dir.is_dir():
        import shutil

        shutil.rmtree(cyt_dir)
        changed = True
        print(f"Removed legacy workspace CYT directory {cyt_dir}", file=sys.stderr)

    return changed
