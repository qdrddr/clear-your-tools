"""Shared fixtures for user-global and workspace MCP migration (frontend-only cleanup)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cyt.hook.cli_invocation import cyt_mcp_cli_script_relpath
from cyt_client.mcp_entry import (
    CYT_MCP_USER_SERVER_KEY,
    CYT_MCP_WORKSPACE_SERVER_KEY,
    build_cyt_mcp_mcp_server_entry,
    workspace_aggregator_config_ref,
)

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "mcp_migration"
SCENARIOS_PATH = FIXTURES_DIR / "scenarios.json"
STALE_USER_CURSOR_MCP_TEMPLATE = FIXTURES_DIR / "stale_user_cursor_mcp.template.json"
STALE_WORKSPACE_CURSOR_MCP_TEMPLATE = FIXTURES_DIR / "stale_workspace_cursor_mcp.template.json"


def load_migration_scenarios() -> list[dict[str, Any]]:
    payload = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))
    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, list):
        raise ValueError(f"invalid scenarios payload in {SCENARIOS_PATH}")
    return [item for item in scenarios if isinstance(item, dict)]


def load_migration_scenario(scope: str) -> dict[str, Any]:
    for scenario in load_migration_scenarios():
        if scenario.get("scope") == scope:
            return scenario
    raise KeyError(f"no migration scenario for scope {scope!r}")


def _backend_servers_from_template(template_path: Path) -> dict[str, Any]:
    template = json.loads(template_path.read_text(encoding="utf-8"))
    servers = template.get("mcpServers")
    if not isinstance(servers, dict):
        raise ValueError(f"invalid template payload in {template_path}")
    return dict(servers)


def stale_user_cursor_mcp_payload(*, repo_root: Path) -> dict[str, Any]:
    """User-global MCP state after backends were migrated but source file was not cleaned."""
    frontend_entry = build_cyt_mcp_mcp_server_entry(
        "cursor",
        dev_repo_root=repo_root,
        dev_script_rel=cyt_mcp_cli_script_relpath(),
    )
    return {
        "mcpServers": {
            CYT_MCP_USER_SERVER_KEY: frontend_entry,
            **_backend_servers_from_template(STALE_USER_CURSOR_MCP_TEMPLATE),
        },
    }


def stale_workspace_cursor_mcp_payload(
    *,
    repo_root: Path,
    workspace_root: Path,
) -> dict[str, Any]:
    """Workspace MCP state after backends were migrated but .cursor/mcp.json was not cleaned."""
    frontend_entry = build_cyt_mcp_mcp_server_entry(
        "cursor",
        dev_repo_root=repo_root,
        dev_script_rel=cyt_mcp_cli_script_relpath(),
        aggregator_config=workspace_aggregator_config_ref("cursor", workspace_root),
    )
    return {
        "mcpServers": {
            CYT_MCP_WORKSPACE_SERVER_KEY: frontend_entry,
            **_backend_servers_from_template(STALE_WORKSPACE_CURSOR_MCP_TEMPLATE),
        },
    }


def write_stale_user_cursor_mcp(path: Path, *, repo_root: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(stale_user_cursor_mcp_payload(repo_root=repo_root), indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def write_stale_workspace_cursor_mcp(
    path: Path,
    *,
    repo_root: Path,
    workspace_root: Path,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            stale_workspace_cursor_mcp_payload(
                repo_root=repo_root,
                workspace_root=workspace_root,
            ),
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def prepare_stale_workspace_migration_tree(
    workspace_root: Path,
    *,
    repo_root: Path,
) -> tuple[Path, Path]:
    """Return workspace ``.cursor/mcp.json`` and ``.agents/cyt/config/mcp/cursor.json`` paths."""
    (workspace_root / ".git").mkdir(parents=True, exist_ok=True)
    project_mcp = workspace_root / ".cursor" / "mcp.json"
    backend_defs = workspace_root / ".agents" / "cyt" / "config" / "mcp" / "cursor.json"
    write_stale_workspace_cursor_mcp(
        project_mcp,
        repo_root=repo_root,
        workspace_root=workspace_root,
    )
    return project_mcp, backend_defs
