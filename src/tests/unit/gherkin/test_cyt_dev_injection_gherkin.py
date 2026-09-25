"""Gherkin steps for cyt development-mode hook and MCP command injection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pytest_bdd import given, scenarios, then, when

import cyt.hook.install_scope as install_scope
from cyt.hook import setup_wizard as hook_setup
from cyt.hook.cli_invocation import (
    HookCliInvocation,
    build_uv_run_dev_command,
    cyt_client_cli_script_relpath,
    cyt_daemon_start_command,
    cyt_mcp_cli_script_relpath,
    prefix_agent_hook_command,
    repo_root_from_proxy_cli_script,
)
from cyt.hook.install_scope import CytInstallScope
from cyt.tools import cyt_mcp_setup
from cyt_client.mcp_entry import (
    CYT_MCP_USER_SERVER_KEY,
    CYT_MCP_WORKSPACE_SERVER_KEY,
    build_cyt_mcp_mcp_server_entry,
    user_aggregator_config_ref,
)
from cyt_client.pairing import repair_pairing
from tests.support.mcp_migration_fixtures import (
    load_migration_scenario,
    prepare_stale_workspace_migration_tree,
    write_stale_user_cursor_mcp,
)
from tests.unit.gherkin.conftest import GherkinContext

FEATURES = Path(__file__).resolve().parent / "features" / "cyt_dev_injection.feature"
scenarios(str(FEATURES))

pytestmark = pytest.mark.gherkin


def _repo_root() -> Path:
    repo_root = repo_root_from_proxy_cli_script()
    assert repo_root is not None
    return repo_root


@given("cyt hook development mode for the current repo")
def given_dev_mode(gherkin_context: GherkinContext) -> None:
    repo_root = _repo_root()
    gherkin_context.payload["repo_root"] = repo_root
    gherkin_context.payload["invocation"] = HookCliInvocation(mode="dev", repo_root=repo_root)


@when("cursor hook entries are built for development mode")
def when_build_hook_entries(gherkin_context: GherkinContext) -> None:
    invocation = gherkin_context.payload["invocation"]
    gherkin_context.payload["client_command"] = hook_setup.cyt_client_entry(
        agent="cursor",
        invocation=invocation,
    )["command"]
    gherkin_context.payload["daemon_command"] = hook_setup.cyt_daemon_start_entry(
        agent="cursor",
        invocation=invocation,
    )["command"]


@then("the cyt-client hook command should use uv run from the repo root")
def then_client_hook_uses_uv(gherkin_context: GherkinContext) -> None:
    repo_root = gherkin_context.payload["repo_root"]
    expected = prefix_agent_hook_command(
        build_uv_run_dev_command(repo_root, cyt_client_cli_script_relpath()),
    )
    assert gherkin_context.payload["client_command"] == expected
    assert str(repo_root) in gherkin_context.payload["client_command"]


@then("the daemon start hook command should use uv run from the repo root")
def then_daemon_hook_uses_uv(gherkin_context: GherkinContext) -> None:
    repo_root = gherkin_context.payload["repo_root"]
    invocation = gherkin_context.payload["invocation"]
    expected = prefix_agent_hook_command(cyt_daemon_start_command(invocation=invocation))
    assert gherkin_context.payload["daemon_command"] == expected
    assert str(repo_root) in gherkin_context.payload["daemon_command"]


@when("cyt-mcp cursor MCP entry is written for development mode")
def when_write_dev_mcp_entry(
    gherkin_context: GherkinContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mcp_path = tmp_path / "mcp.json"
    monkeypatch.setitem(cyt_mcp_setup._AGENT_SOURCE_PATHS, "cursor", mcp_path)
    invocation = gherkin_context.payload["invocation"]
    cyt_mcp_setup.write_agent_cyt_mcp_entry("cursor", invocation=invocation, transport="stdio")
    gherkin_context.payload["mcp_path"] = mcp_path


@then("cursor mcp.json cyt-mcp entry should use uv run from the repo root")
def then_mcp_entry_uses_uv(gherkin_context: GherkinContext) -> None:
    repo_root = gherkin_context.payload["repo_root"]
    mcp_path = gherkin_context.payload.get("mcp_path")
    if mcp_path is not None:
        payload = json.loads(mcp_path.read_text(encoding="utf-8"))
        entry = payload["mcpServers"][CYT_MCP_USER_SERVER_KEY]
    else:
        entry = gherkin_context.payload["mcp_entry"]

    aggregator_config = user_aggregator_config_ref()
    expected = build_cyt_mcp_mcp_server_entry(
        "cursor",
        dev_repo_root=repo_root,
        dev_script_rel=cyt_mcp_cli_script_relpath(),
        aggregator_config=aggregator_config,
    )
    assert entry == expected
    from cyt_client.hook_executable import is_uv_hook_command

    assert is_uv_hook_command(entry["command"])
    assert entry["args"][0:3] == ["run", "--directory", str(repo_root)]
    assert entry["args"][3] == cyt_mcp_cli_script_relpath()


@given("cursor hooks.json contains development cyt-client commands")
def given_dev_hooks(gherkin_context: GherkinContext, tmp_path: Path) -> None:
    repo_root = gherkin_context.payload["repo_root"]
    hooks_path = tmp_path / "hooks.json"
    client_command = build_uv_run_dev_command(repo_root, cyt_client_cli_script_relpath())
    hooks_path.write_text(
        json.dumps(
            {
                "version": 1,
                "hooks": {
                    "beforeSubmitPrompt": [{"command": client_command, "timeout": 60}],
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    gherkin_context.payload["hooks_path"] = hooks_path


@given("cursor MCP config has no cyt-mcp entry")
def given_empty_mcp(gherkin_context: GherkinContext, tmp_path: Path) -> None:
    mcp_path = tmp_path / "mcp.json"
    mcp_path.write_text(json.dumps({"mcpServers": {}}) + "\n", encoding="utf-8")
    gherkin_context.payload["mcp_path"] = mcp_path
    aggregator_path = tmp_path / "mcp-aggregator.yaml"
    aggregator_path.write_text(
        "\n".join(
            [
                "default_agent: cursor",
                "transport: stdio",
                "http:",
                "  host: 127.0.0.1",
                "  port: 8765",
                "  mcp_path: /mcp",
                "  catalog_path: /catalog",
                "",
            ],
        ),
        encoding="utf-8",
    )
    gherkin_context.payload["aggregator_path"] = aggregator_path


@when("cyt-client pairing repairs MCP config")
def when_pairing_repairs(
    gherkin_context: GherkinContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hooks_path = gherkin_context.payload["hooks_path"]
    mcp_path = gherkin_context.payload["mcp_path"]
    aggregator_path = gherkin_context.payload["aggregator_path"]
    monkeypatch.setattr("cyt_client.pairing._AGENT_HOOK_PATHS", {"cursor": hooks_path})
    monkeypatch.setattr("cyt_client.pairing._AGENT_MCP_PATHS", {"cursor": mcp_path})
    monkeypatch.setattr(
        "cyt_client.mcp_entry.DEFAULT_AGGREGATOR_PATH",
        aggregator_path,
    )
    monkeypatch.setattr("cyt_client.config.tools_from_includes_cyt_mcp", lambda: True)
    repair_pairing(
        {
            "hook_event_name": "sessionStart",
            "session_id": "dev-pair-session",
            "cursor_version": "1.0",
        },
        verbose=False,
    )
    gherkin_context.payload["mcp_entry"] = json.loads(mcp_path.read_text(encoding="utf-8"))[
        "mcpServers"
    ][CYT_MCP_USER_SERVER_KEY]


@given("cursor mcp.json contains cyt-mcp frontend and a backend server")
def given_mcp_with_frontend_and_backend(
    gherkin_context: GherkinContext,
    tmp_path: Path,
) -> None:
    repo_root = gherkin_context.payload["repo_root"]
    mcp_path = tmp_path / "mcp.json"
    mcp_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "cyt-mcp": build_cyt_mcp_mcp_server_entry(
                        "cursor",
                        dev_repo_root=repo_root,
                        dev_script_rel=cyt_mcp_cli_script_relpath(),
                    ),
                    "wiseinfotec": {"url": "https://mcp.example.com/mcp"},
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    gherkin_context.payload["source_mcp_path"] = mcp_path


@when("cyt-mcp setup migrates backends for cursor")
def when_setup_migrates_backends(
    gherkin_context: GherkinContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_dir = tmp_path / "backends"
    aggregator_path = tmp_path / "mcp-aggregator.yaml"
    monkeypatch.setitem(
        cyt_mcp_setup._AGENT_SOURCE_PATHS,
        "cursor",
        gherkin_context.payload["source_mcp_path"],
    )
    monkeypatch.setattr(cyt_mcp_setup, "DEFAULT_MCP_DIR", target_dir)
    monkeypatch.setattr(cyt_mcp_setup, "DEFAULT_MCP_CONFIG_PATH", aggregator_path)
    monkeypatch.setattr(cyt_mcp_setup, "DEFAULT_AGGREGATOR_PATH", aggregator_path)
    invocation = gherkin_context.payload["invocation"]
    cyt_mcp_setup.setup_cyt_mcp_for_agent("cursor", invocation=invocation, transport="stdio")
    gherkin_context.payload["backend_path"] = target_dir / "cursor.json"


@then("cursor agent mcp.json should contain only cyt-mcp")
def then_agent_mcp_frontend_only(gherkin_context: GherkinContext) -> None:
    payload = json.loads(gherkin_context.payload["source_mcp_path"].read_text(encoding="utf-8"))
    assert set(payload["mcpServers"]) == {CYT_MCP_USER_SERVER_KEY}


@when("backend MCP servers are migrated for cursor")
def when_migrate_backends(
    gherkin_context: GherkinContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_dir = tmp_path / "backends"
    monkeypatch.setitem(
        cyt_mcp_setup._AGENT_SOURCE_PATHS,
        "cursor",
        gherkin_context.payload["source_mcp_path"],
    )
    monkeypatch.setattr(cyt_mcp_setup, "DEFAULT_MCP_DIR", target_dir)
    cyt_mcp_setup.migrate_agent_backends("cursor")
    gherkin_context.payload["backend_path"] = target_dir / "cursor.json"


@then("migrated backends should exclude cyt-mcp frontend")
def then_backends_exclude_self(gherkin_context: GherkinContext) -> None:
    payload = json.loads(gherkin_context.payload["backend_path"].read_text(encoding="utf-8"))
    assert "cyt-mcp" not in payload["mcpServers"]


@then("migrated backends should include the backend server")
def then_backends_include_backend(gherkin_context: GherkinContext) -> None:
    payload = json.loads(gherkin_context.payload["backend_path"].read_text(encoding="utf-8"))
    assert "wiseinfotec" in payload["mcpServers"]


@given("cursor mcp.json contains equivalent cyt-mcp-usr and a stale backend server")
@given("cursor's user-scoped mcp.json contains equivalent cyt-mcp-usr and a stale backend server")
def given_mcp_with_equivalent_frontend_and_stale_backend(
    gherkin_context: GherkinContext,
    tmp_path: Path,
) -> None:
    repo_root = gherkin_context.payload["repo_root"]
    scenario = load_migration_scenario("user")
    mcp_path = tmp_path / "mcp.json"
    write_stale_user_cursor_mcp(mcp_path, repo_root=repo_root)
    gherkin_context.payload["source_mcp_path"] = mcp_path
    gherkin_context.payload["stale_backend_name"] = scenario["backend_server"]


@then("cursor's user-scoped mcp.json should contain only cyt-mcp-usr")
def then_user_scoped_mcp_frontend_only(gherkin_context: GherkinContext) -> None:
    payload = json.loads(gherkin_context.payload["source_mcp_path"].read_text(encoding="utf-8"))
    assert set(payload["mcpServers"]) == {CYT_MCP_USER_SERVER_KEY}


@then("migrated backends should include the stale backend server")
def then_backends_include_stale_backend(gherkin_context: GherkinContext) -> None:
    backend_name = gherkin_context.payload["stale_backend_name"]
    payload = json.loads(gherkin_context.payload["backend_path"].read_text(encoding="utf-8"))
    assert backend_name in payload["mcpServers"]


@given(
    "the workspace .cursor/mcp.json contains equivalent cyt-mcp-ws and a stale backend server",
)
def given_workspace_mcp_with_equivalent_frontend_and_stale_backend(
    gherkin_context: GherkinContext,
    tmp_path: Path,
) -> None:
    repo_root = gherkin_context.payload["repo_root"]
    scenario = load_migration_scenario("workspace")
    workspace_root = tmp_path / "workspace"
    project_mcp, backend_defs = prepare_stale_workspace_migration_tree(
        workspace_root,
        repo_root=repo_root,
    )
    gherkin_context.payload["workspace_root"] = workspace_root
    gherkin_context.payload["source_mcp_path"] = project_mcp
    gherkin_context.payload["backend_path"] = backend_defs
    gherkin_context.payload["stale_backend_name"] = scenario["backend_server"]


@when("cyt-mcp setup migrates workspace backends for cursor")
def when_setup_migrates_workspace_backends(
    gherkin_context: GherkinContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = gherkin_context.payload["workspace_root"]
    scope = CytInstallScope(workspace_root=workspace_root.resolve())
    monkeypatch.setattr(
        install_scope.CytInstallScope,
        "from_cwd",
        classmethod(lambda cls, *, cwd=None: scope),
    )
    invocation = gherkin_context.payload["invocation"]
    cyt_mcp_setup.setup_cyt_mcp_for_agent(
        "cursor",
        invocation=invocation,
        transport="stdio",
        configure_user=False,
        configure_workspace=True,
        scope=scope,
    )


@then("the workspace .cursor/mcp.json should contain only cyt-mcp-ws")
def then_workspace_mcp_frontend_only(gherkin_context: GherkinContext) -> None:
    payload = json.loads(gherkin_context.payload["source_mcp_path"].read_text(encoding="utf-8"))
    assert set(payload["mcpServers"]) == {CYT_MCP_WORKSPACE_SERVER_KEY}


@then(
    "workspace backends at .agents/cyt/config/mcp/cursor.json should include the stale backend server",
)
def then_workspace_backends_include_stale_backend(gherkin_context: GherkinContext) -> None:
    backend_name = gherkin_context.payload["stale_backend_name"]
    payload = json.loads(gherkin_context.payload["backend_path"].read_text(encoding="utf-8"))
    assert backend_name in payload["mcpServers"]
