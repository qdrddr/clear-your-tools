"""Unit tests: workspace MCP server defs (.agents/cyt/config/mcp/cursor.json) contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import cyt.tools.cyt_mcp_setup as cyt_mcp_setup
from cyt.hook.install_scope import CytInstallScope
from cyt_mcp.config import load_aggregator_config, load_mcp_servers
from tests.support.cyt_mcp_server_defs_fixtures import (
    assert_mcp_server_defs_is_json,
    load_server_defs_scenario,
    load_yaml_cursor_defs_fixture,
    materialize_workspace_with_mcp_config,
    repo_root_from_tests,
    workspace_server_defs_path,
)


def test_yaml_fixture_is_not_strict_json() -> None:
    text = load_yaml_cursor_defs_fixture()
    with pytest.raises(json.JSONDecodeError):
        json.loads(text)
    assert text.lstrip().startswith("mcpServers:")


def test_valid_cursor_fixture_satisfies_json_contract(tmp_path: Path) -> None:
    workspace = materialize_workspace_with_mcp_config(tmp_path, cursor_defs="valid")
    scenario = load_server_defs_scenario("workspace_cursor_json_contract")
    defs_path = workspace / scenario.raw["backend_defs_relpath"]
    payload = assert_mcp_server_defs_is_json(defs_path)
    for key in scenario.raw["expected_server_keys"]:
        assert key in payload["mcpServers"]


def test_load_mcp_servers_yaml_fallback_uses_fixture(tmp_path: Path) -> None:
    """Defensive recovery path: YAML bodies in *.json paths must still load backends."""
    scenario = load_server_defs_scenario("yaml_cursor_defs_recovery")
    workspace = materialize_workspace_with_mcp_config(tmp_path, cursor_defs="yaml")
    defs_path = workspace / scenario.raw["backend_defs_relpath"]
    servers = load_mcp_servers(defs_path)
    assert set(servers) == set(scenario.raw["expected_server_keys"])


def test_migrate_agent_backends_from_writes_json_server_defs(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    source = tmp_path / "source_mcp.json"
    source.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "example-backend": {"command": "echo", "args": ["ok"]},
                },
            },
        ),
        encoding="utf-8",
    )
    target = workspace / ".agents" / "cyt" / "config" / "mcp" / "cursor.json"

    cyt_mcp_setup.migrate_agent_backends_from(
        source,
        target,
        agent="cursor",
        permission_scope="workspace",
        workspace_root=workspace,
    )

    payload = assert_mcp_server_defs_is_json(target)
    assert "example-backend" in payload["mcpServers"]


def test_setup_cyt_mcp_workspace_for_agent_writes_json_server_defs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    project_mcp = workspace / ".cursor" / "mcp.json"
    project_mcp.parent.mkdir(parents=True)
    project_mcp.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "backend-a": {"command": "echo", "args": ["a"]},
                },
            },
        ),
        encoding="utf-8",
    )
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    scope = CytInstallScope(workspace_root=workspace)

    cyt_mcp_setup.setup_cyt_mcp_workspace_for_agent(
        "cursor",
        scope=scope,
        migrate_backends=True,
        verify_only=True,
    )

    defs_path = workspace_server_defs_path(workspace)
    payload = assert_mcp_server_defs_is_json(defs_path)
    assert "backend-a" in payload["mcpServers"]


def test_load_aggregator_config_with_valid_json_cursor_defs(tmp_path: Path) -> None:
    workspace = materialize_workspace_with_mcp_config(tmp_path, cursor_defs="valid")
    config = load_aggregator_config(
        agent="cursor",
        aggregator_path=workspace / ".agents/cyt/config/mcp-config.yaml",
        workspace_folder=workspace,
    )
    assert config.catalog_scope == "workspace"
    assert set(config.mcp_servers) >= {"semble", "gitnexus"}


def test_load_aggregator_config_with_yaml_cursor_defs_fallback(tmp_path: Path) -> None:
    workspace = materialize_workspace_with_mcp_config(tmp_path, cursor_defs="yaml")
    config = load_aggregator_config(
        agent="cursor",
        aggregator_path=workspace / ".agents/cyt/config/mcp-config.yaml",
        workspace_folder=workspace,
    )
    assert set(config.mcp_servers) == {"semble", "gitnexus"}


def test_dev_repo_workspace_cursor_json_is_valid_json() -> None:
    """Guard the canonical dev-repo workspace backend defs file format."""
    repo_root = repo_root_from_tests()
    defs_path = repo_root / ".agents" / "cyt" / "config" / "mcp" / "cursor.json"
    if not defs_path.is_file():
        pytest.skip("dev repo workspace cursor.json not present")
    payload = assert_mcp_server_defs_is_json(defs_path)
    assert payload["mcpServers"]
