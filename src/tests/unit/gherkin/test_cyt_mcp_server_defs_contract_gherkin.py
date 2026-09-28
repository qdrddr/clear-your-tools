"""Gherkin steps for workspace MCP server defs JSON contract."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from pytest_bdd import given, scenarios, then, when

import cyt.tools.cyt_mcp_setup as cyt_mcp_setup
from cyt.hook.install_scope import CytInstallScope
from cyt_mcp.config import load_aggregator_config
from tests.support.cyt_mcp_server_defs_fixtures import (
    assert_mcp_server_defs_is_json,
    load_server_defs_scenario,
    materialize_workspace_with_mcp_config,
    workspace_server_defs_path,
)
from tests.unit.gherkin.conftest import GherkinContext

FEATURES = Path(__file__).resolve().parent / "features" / "cyt_mcp_server_defs_contract.feature"
scenarios(str(FEATURES))

pytestmark = pytest.mark.gherkin


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    yield


@given("a git workspace with backend MCP servers in .cursor/mcp.json")
def given_workspace_with_project_mcp(gherkin_context: GherkinContext, tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    project_mcp = workspace / ".cursor" / "mcp.json"
    project_mcp.parent.mkdir(parents=True)
    project_mcp.write_text(
        json.dumps({"mcpServers": {"backend-a": {"command": "echo", "args": ["a"]}}}),
        encoding="utf-8",
    )
    gherkin_context.payload["workspace"] = workspace.resolve()


@when("cyt-mcp workspace setup migrates backends for cursor")
def when_workspace_setup_migrates(gherkin_context: GherkinContext) -> None:
    workspace = gherkin_context.payload["workspace"]
    scope = CytInstallScope(workspace_root=workspace)
    cyt_mcp_setup.setup_cyt_mcp_workspace_for_agent(
        "cursor",
        scope=scope,
        migrate_backends=True,
        verify_only=True,
    )


@then("workspace server defs at .agents/cyt/config/mcp/cursor.json should be valid JSON")
def then_workspace_server_defs_valid_json(gherkin_context: GherkinContext) -> None:
    workspace = gherkin_context.payload["workspace"]
    scenario = load_server_defs_scenario("workspace_cursor_json_contract")
    defs_path = workspace / scenario.raw["backend_defs_relpath"]
    payload = assert_mcp_server_defs_is_json(defs_path)
    assert "backend-a" in payload["mcpServers"]


@given("a workspace with valid JSON MCP server defs")
def given_valid_json_server_defs(gherkin_context: GherkinContext, tmp_path: Path) -> None:
    workspace = materialize_workspace_with_mcp_config(tmp_path, cursor_defs="valid")
    gherkin_context.payload["workspace"] = workspace
    gherkin_context.payload["server_defs_scenario"] = load_server_defs_scenario(
        "workspace_cursor_json_contract",
    )


@given("a workspace with YAML MCP server defs stored at cursor.json")
def given_yaml_server_defs(gherkin_context: GherkinContext, tmp_path: Path) -> None:
    workspace = materialize_workspace_with_mcp_config(tmp_path, cursor_defs="yaml")
    gherkin_context.payload["workspace"] = workspace
    gherkin_context.payload["server_defs_scenario"] = load_server_defs_scenario(
        "yaml_cursor_defs_recovery",
    )


@when("workspace aggregator config is loaded for cursor")
def when_load_aggregator_config(gherkin_context: GherkinContext) -> None:
    workspace = gherkin_context.payload["workspace"]
    config = load_aggregator_config(
        agent="cursor",
        aggregator_path=workspace / ".agents/cyt/config/mcp-config.yaml",
        workspace_folder=workspace,
    )
    gherkin_context.payload["aggregator_config"] = config


@then("loaded MCP server keys should include configured backend servers")
def then_loaded_server_keys(gherkin_context: GherkinContext) -> None:
    scenario = gherkin_context.payload["server_defs_scenario"]
    config = gherkin_context.payload["aggregator_config"]
    expected = set(scenario.raw["expected_server_keys"])
    assert expected <= set(config.mcp_servers)
