"""Wizard helpers for cyt-mcp aggregator setup and agent MCP migration."""

from __future__ import annotations

import json
import sys
import tomllib
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
    CytInstallScope,
)
from cyt.permissions.paths import PermissionScope
from cyt.proxy.setup_wizard import _prompt
from cyt_client.mcp_entry import (
    CURSOR_WORKSPACE_FOLDER,
    CYT_MCP_FRONTEND_SERVER_KEYS,
    CYT_MCP_SERVER_KEY,
    CYT_MCP_WORKSPACE_SERVER_KEY,
    LEGACY_CYT_MCP_SERVER_KEY,
    LEGACY_CYT_MCP_USER_SERVER_KEY,
    LEGACY_CYT_MCP_WORKSPACE_SERVER_KEY,
    CytMcpTransport,
    backend_mcp_servers,
    build_cyt_mcp_mcp_server_entry,
    codex_cyt_mcp_toml_block,
    load_aggregator_transport_settings,
    normalize_cyt_mcp_transport,
    workspace_aggregator_config_ref,
)

DEFAULT_MCP_CONFIG_PATH = Path("~/.config/cyt/mcp-config.yaml")
DEFAULT_AGGREGATOR_PATH = DEFAULT_MCP_CONFIG_PATH  # deprecated alias
DEFAULT_MCP_DIR = Path("~/.config/cyt/mcp")
DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8765
DEFAULT_WORKSPACE_HTTP_PORT = 8766
DEFAULT_MCP_PATH = "/mcp"
DEFAULT_CATALOG_PATH = "/catalog"

_AGENT_SOURCE_PATHS: dict[str, Path] = GLOBAL_AGENT_MCP_PATHS


def _cyt_mcp_workspace_cwd(agent: str) -> str | None:
    if agent == "cursor":
        return CURSOR_WORKSPACE_FOLDER
    return None


def _remove_cyt_mcp_frontend_keys(servers: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    removed = [key for key in servers if key in CYT_MCP_FRONTEND_SERVER_KEYS]
    if not removed:
        return servers, []
    cleaned = {
        key: spec for key, spec in servers.items() if key not in CYT_MCP_FRONTEND_SERVER_KEYS
    }
    return cleaned, removed


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


def _extract_mcp_servers_from_codex_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    servers = payload.get("mcp_servers")
    return servers if isinstance(servers, dict) else {}


def _extract_mcp_servers_from_agent_path(path: Path, agent: str) -> dict[str, Any]:
    if (agent or "cursor").strip() == "codex":
        return _extract_mcp_servers_from_codex_toml(path)
    return _extract_mcp_servers_from_json(path)


def _backend_servers_from_agent_path(path: Path, agent: str) -> dict[str, Any]:
    return backend_mcp_servers(_extract_mcp_servers_from_agent_path(path, agent))


def has_migratable_mcp_backends(agent: str, scope: CytInstallScope) -> bool:
    """Return True when at least one non-cyt-mcp backend exists in cyt storage or agent MCP."""
    agent = agent.strip() or "cursor"
    defs_locations: list[Path] = [scope.user_server_defs_path(agent)]
    workspace_defs = scope.resolve_workspace_server_defs_path(agent)
    if workspace_defs is not None:
        defs_locations.append(workspace_defs)
    for path in defs_locations:
        if backend_mcp_servers(_extract_mcp_servers_from_json(path)):
            return True
    agent_locations: list[Path] = [scope.global_agent_mcp_path(agent)]
    workspace_mcp = scope.workspace_agent_mcp_path(agent)
    if workspace_mcp is not None:
        agent_locations.append(workspace_mcp)
    for path in agent_locations:
        if _backend_servers_from_agent_path(path, agent):
            return True
    return False


def _strip_codex_mcp_server_sections(text: str, server_keys: frozenset[str]) -> tuple[str, bool]:
    changed = False
    for key in server_keys:
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
    return text, changed


def _codex_mcp_server_toml_block(server_key: str, spec: dict[str, Any]) -> str:
    section = f"[mcp_servers.{server_key}]"
    url = spec.get("url")
    if isinstance(url, str) and url.strip():
        return f'\n{section}\nurl = "{url.strip()}"\n'
    command = str(spec.get("command", "")).strip()
    args = spec.get("args")
    if not isinstance(args, list):
        args = []
    lines = [f"\n{section}"]
    if command:
        lines.append(f'command = "{command}"')
    if args:
        lines.append(f"args = {json.dumps(args)}")
    cwd = spec.get("cwd")
    if isinstance(cwd, str) and cwd.strip():
        lines.append(f'cwd = "{cwd.strip()}"')
    env = spec.get("env")
    if isinstance(env, dict) and env:
        lines.append("env = {")
        for key, value in env.items():
            lines.append(f'  {json.dumps(str(key))} = {json.dumps(str(value))}')
        lines.append("}")
    enabled = spec.get("enabled")
    if isinstance(enabled, bool):
        lines.append(f"enabled = {'true' if enabled else 'false'}")
    server_type = spec.get("type")
    if isinstance(server_type, str) and server_type.strip():
        lines.append(f'type = "{server_type.strip()}"')
    return "\n".join(lines) + "\n"


def _restore_json_mcp_backends(backends: dict[str, Any], target_path: Path) -> bool:
    if target_path.is_file():
        try:
            raw = json.loads(target_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
    else:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    existing = raw.get("mcpServers")
    if not isinstance(existing, dict):
        existing = {}
    cleaned, removed = _remove_cyt_mcp_frontend_keys(dict(existing))
    merged = dict(cleaned)
    changed = bool(removed)
    for name, spec in backends.items():
        if merged.get(name) != spec:
            changed = True
        merged[name] = spec
    if not changed:
        return False
    raw["mcpServers"] = merged
    _atomic_write_text(target_path, json.dumps(raw, indent=2) + "\n")
    print(f"Restored MCP backends to {target_path}", file=sys.stderr)
    return True


def _restore_codex_mcp_backends(backends: dict[str, Any], target_path: Path) -> bool:
    text = target_path.read_text(encoding="utf-8") if target_path.is_file() else ""
    text, changed = _strip_codex_mcp_server_sections(text, CYT_MCP_FRONTEND_SERVER_KEYS)
    for name, spec in backends.items():
        if not isinstance(spec, dict):
            continue
        marker = f"[mcp_servers.{name}]"
        if marker in text:
            before, _, after = text.partition(marker)
            next_section = after.find("\n[mcp_servers.")
            if next_section >= 0:
                text = before.rstrip() + after[next_section:]
            else:
                text = before.rstrip() + "\n"
            changed = True
        block = _codex_mcp_server_toml_block(name, spec)
        if block.strip() not in text:
            changed = True
        text = text.rstrip() + block
    if not changed:
        return False
    _atomic_write_text(target_path, text)
    print(f"Restored MCP backends to {target_path}", file=sys.stderr)
    return True


def restore_agent_mcp_backends_from(
    defs_path: Path,
    target_path: Path,
    *,
    agent: str = "cursor",
) -> bool:
    """Merge backends from cyt defs into agent MCP config; strip cyt-mcp frontend keys."""
    agent = agent.strip() or "cursor"
    backends = backend_mcp_servers(_extract_mcp_servers_from_json(defs_path))
    if agent == "codex":
        return _restore_codex_mcp_backends(backends, target_path)
    return _restore_json_mcp_backends(backends, target_path)


def restore_user_cyt_mcp_for_agent(agent: str, scope: CytInstallScope) -> bool:
    """Restore user-scoped agent MCP from cyt backend defs and remove cyt-mcp frontend."""
    agent = agent.strip() or "cursor"
    defs_path = scope.user_server_defs_path(agent)
    target_path = scope.global_agent_mcp_path(agent)
    if defs_path.is_file():
        return restore_agent_mcp_backends_from(defs_path, target_path, agent=agent)
    if agent == "codex":
        return _restore_codex_mcp_backends({}, target_path)
    return _restore_json_mcp_backends({}, target_path)


def restore_workspace_cyt_mcp_for_agent(agent: str, scope: CytInstallScope) -> bool:
    """Restore workspace agent MCP from cyt backend defs and remove cyt-mcp frontend."""
    if not scope.has_workspace:
        return False
    agent = agent.strip() or "cursor"
    target_path = scope.workspace_agent_mcp_path(agent)
    if target_path is None:
        return False
    defs_path = scope.resolve_workspace_server_defs_path(agent)
    if defs_path is not None and defs_path.is_file():
        return restore_agent_mcp_backends_from(defs_path, target_path, agent=agent)
    if agent == "codex":
        return _restore_codex_mcp_backends({}, target_path)
    return _restore_json_mcp_backends({}, target_path)


def migrate_agent_backends_from(
    source_path: Path,
    target_path: Path,
    *,
    agent: str = "cursor",
    permission_scope: PermissionScope = "user",
    workspace_root: Path | None = None,
) -> Path:
    """Copy backend MCP servers from *source_path* into *target_path*."""
    from cyt.permissions.mcp_defs import disabled_server_names, import_disabled_servers_to_deny

    servers = _backend_servers_from_agent_path(source_path, agent)
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
            scope="workspace" if permission_scope == "workspace" else "user",
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
        permission_scope="user",
    )


def write_mcp_config_yaml(
    agent: str,
    *,
    backends_path: Path | None = None,
    transport: CytMcpTransport = "stdio",
    verify_only: bool = False,
    aggregator_path: Path | None = None,
    http_port: int | None = None,
) -> Path:
    return write_mcp_config_yaml_at(
        aggregator_path or DEFAULT_MCP_CONFIG_PATH.expanduser(),
        agent,
        backends_path=backends_path,
        transport=transport,
        verify_only=verify_only,
        http_port=http_port or DEFAULT_HTTP_PORT,
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
    """Deprecated alias for :func:`write_mcp_config_yaml`."""
    return write_mcp_config_yaml(
        agent,
        backends_path=backends_path,
        transport=transport,
        verify_only=verify_only,
        aggregator_path=aggregator_path,
        http_port=http_port,
    )


def _workspace_agent_mcp_yaml_ref(aggregator_path: Path, backends_path: Path) -> str:
    """Return a portable agents.* path relative to the workspace aggregator directory."""
    agg_dir = aggregator_path.expanduser().resolve().parent
    backend_resolved = backends_path.expanduser().resolve()
    try:
        return backend_resolved.relative_to(agg_dir).as_posix()
    except ValueError:
        return backend_resolved.as_posix()


def write_mcp_config_yaml_at(
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
        from cyt.migrations.mcp_config import DEFAULT_MCP_AGENT_CONFIG_REFS

        lines.extend(
            f"  {agent_name}: {DEFAULT_MCP_AGENT_CONFIG_REFS[agent_name]}"
            for agent_name in ("cursor", "claude", "codex")
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
            "pruning:",
            "  tools:",
            "    stub: basic",
            "    stub_by_agent:",
            "      codex: codex",
            "      cursor: basic",
            "      claude: basic",
            "    stubs:",
            "      - name: basic",
            "        always:",
            "          tool: [name]",
            "          required_properties: [name]",
            "          optional_properties: []",
            "      - name: codex",
            "        always:",
            "          tool: [name, description]",
            "          required_properties: [name]",
            "          optional_properties: []",
            "",
        ],
    )
    _atomic_write_text(path, "\n".join(lines))
    print(f"\nWrote {path} (agent mapping includes {backends})", file=sys.stderr)
    return path


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
    """Deprecated alias for :func:`write_mcp_config_yaml_at`."""
    return write_mcp_config_yaml_at(
        path,
        agent,
        backends_path=backends_path,
        transport=transport,
        verify_only=verify_only,
        http_port=http_port,
        workspace_scoped=workspace_scoped,
    )


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
        if server_key == CYT_MCP_SERVER_KEY:
            for legacy_key in (
                LEGACY_CYT_MCP_USER_SERVER_KEY,
                LEGACY_CYT_MCP_SERVER_KEY,
                CYT_MCP_WORKSPACE_SERVER_KEY,
                LEGACY_CYT_MCP_WORKSPACE_SERVER_KEY,
            ):
                servers.pop(legacy_key, None)
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
    workspace_root: Path | None = None,
) -> None:
    scope = CytInstallScope.from_cwd()
    write_agent_cyt_mcp_entry_at(
        scope.global_agent_mcp_path(agent),
        agent,
        invocation=invocation,
        transport=transport,
        server_key=CYT_MCP_SERVER_KEY,
        aggregator_config=workspace_aggregator_config_ref(agent, workspace_root),
        workspace_cwd=_cyt_mcp_workspace_cwd(agent),
        frontend_only=frontend_only,
    )


def _workspace_aggregator_config_path(scope: CytInstallScope, agent: str) -> str:
    return workspace_aggregator_config_ref(agent, scope.workspace_root)


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
    if not resolved.is_dev and not has_migratable_mcp_backends(agent, scope):
        return

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

    remove_project_cyt_mcp_for_agent(agent, scope)


def remove_project_cyt_mcp_for_agent(agent: str, scope: CytInstallScope) -> bool:
    """Remove cyt-mcp frontend entries from project agent MCP config."""
    if not scope.has_workspace:
        return False
    mcp_path = scope.workspace_agent_mcp_path(agent)
    if mcp_path is None or not mcp_path.is_file():
        return False
    agent = agent.strip() or "cursor"
    if agent == "codex":
        text = mcp_path.read_text(encoding="utf-8")
        text, changed = _strip_codex_mcp_server_sections(text, CYT_MCP_FRONTEND_SERVER_KEYS)
        if not changed:
            return False
        _atomic_write_text(mcp_path, text)
        print(f"Removed cyt-mcp frontend from {mcp_path}", file=sys.stderr)
        return True
    try:
        raw = json.loads(mcp_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(raw, dict):
        return False
    servers = raw.get("mcpServers")
    if not isinstance(servers, dict):
        return False
    cleaned, removed = _remove_cyt_mcp_frontend_keys(dict(servers))
    if not removed:
        return False
    raw["mcpServers"] = cleaned
    _atomic_write_text(mcp_path, json.dumps(raw, indent=2) + "\n")
    print(f"Removed {', '.join(removed)} from {mcp_path}", file=sys.stderr)
    return True


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
    if not resolved.is_dev and not has_migratable_mcp_backends(agent, install_scope):
        return

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
        workspace_root=install_scope.workspace_root,
    )

    if not install_scope.has_workspace:
        return

    configure_workspace = configure_workspace if configure_workspace is not None else True

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
    """Restore workspace agent MCP from cyt backend defs; return True when anything changed."""
    return restore_workspace_cyt_mcp_for_agent(agent, scope)
