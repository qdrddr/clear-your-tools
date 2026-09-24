"""Gherkin steps for fish-safe hook wrappers and corrupt MCP config recovery."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pytest_bdd import given, scenarios, then, when

from cyt.hook import setup_wizard as hook_setup
from cyt.hook.cli_invocation import HookCliInvocation, repo_root_from_proxy_cli_script
from cyt.hook.install_scope import CytInstallScope
from cyt.migrations.workspace_paths import ensure_canonical_workspace_mcp_config
from cyt.tools import cyt_mcp_setup
from tests.support.hook_shell_mcp_recovery_fixtures import (
    assert_valid_mcp_config_yaml,
    write_corrupt_workspace_mcp_config,
)
from tests.unit.gherkin.conftest import GherkinContext

FEATURES = Path(__file__).resolve().parent / "features" / "hook_shell_mcp_recovery.feature"
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


@when("cursor hook entries are built with shell wrappers")
def when_build_wrapper_hook_entries(gherkin_context: GherkinContext) -> None:
    invocation = gherkin_context.payload["invocation"]
    entries = hook_setup.cursor_hook_entries(agent="cursor", invocation=invocation)
    gherkin_context.payload["client_command"] = entries["before_submit"]["command"]
    gherkin_context.payload["client_wrapper"] = Path(entries["before_submit"]["command"])


@then("hook commands should point at shell wrapper scripts")
def then_hook_commands_use_wrappers(gherkin_context: GherkinContext) -> None:
    command = str(gherkin_context.payload["client_command"])
    if sys.platform == "win32":
        assert command.endswith("cyt-client-dev.cmd")
    else:
        assert command.endswith("cyt-client-dev.sh")
    wrapper = Path(command)
    assert wrapper.is_file()


@then("hook commands should not use inline CYT_WORKSPACE env prefixes")
def then_no_inline_env_prefix(gherkin_context: GherkinContext) -> None:
    command = str(gherkin_context.payload["client_command"])
    assert "CYT_WORKSPACE=" not in command
    assert "${workspaceFolder}" not in command


@then("the client wrapper script should resolve workspace from Cursor env vars")
def then_wrapper_resolves_cursor_env(gherkin_context: GherkinContext) -> None:
    wrapper = gherkin_context.payload["client_wrapper"]
    assert isinstance(wrapper, Path)
    text = wrapper.read_text(encoding="utf-8")
    if sys.platform == "win32":
        assert "CURSOR_PROJECT_DIR" in text or "CYT_WORKSPACE" in text
    else:
        assert text.startswith("#!/usr/bin/env bash")
        assert "CURSOR_PROJECT_DIR" in text
        assert "_resolve_cyt_workspace" in text


@given("a consumer workspace with corrupt workspace mcp-config")
def given_corrupt_workspace_mcp_config(
    gherkin_context: GherkinContext,
    tmp_path: Path,
) -> None:
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    (consumer / ".git").mkdir()
    scope = CytInstallScope(workspace_root=consumer.resolve())
    canonical = scope.workspace_all_agents_cyt_mcp_config_path()
    defs_path = scope.workspace_all_agents_cyt_mcp_defs_path("cursor")
    assert canonical is not None and defs_path is not None
    defs_path.parent.mkdir(parents=True, exist_ok=True)
    defs_path.write_text('{"mcpServers": {}}\n', encoding="utf-8")
    write_corrupt_workspace_mcp_config(canonical)
    gherkin_context.payload["scope"] = scope
    gherkin_context.payload["canonical"] = canonical
    gherkin_context.payload["defs_path"] = defs_path


@when("workspace MCP canonicalization runs")
def when_canonicalize_workspace_mcp(gherkin_context: GherkinContext) -> None:
    scope = gherkin_context.payload["scope"]
    gherkin_context.payload["canonical_result"] = ensure_canonical_workspace_mcp_config(scope)


@then("the corrupt mcp-config should be backed up and removed")
def then_corrupt_config_removed(gherkin_context: GherkinContext) -> None:
    canonical = gherkin_context.payload["canonical"]
    scope = gherkin_context.payload["scope"]
    assert gherkin_context.payload["canonical_result"] == canonical
    assert not canonical.is_file()
    workspace_root = scope.workspace_root
    assert workspace_root is not None
    backups = list(workspace_root.rglob("mcp-config.yaml.corrupt.*"))
    assert backups


@when("workspace MCP aggregator is rewritten for cursor")
def when_rewrite_workspace_mcp_aggregator(gherkin_context: GherkinContext) -> None:
    canonical = gherkin_context.payload["canonical"]
    defs_path = gherkin_context.payload["defs_path"]
    cyt_mcp_setup.write_mcp_aggregator_yaml_at(
        canonical,
        "cursor",
        backends_path=defs_path,
        workspace_scoped=True,
        http_port=cyt_mcp_setup.DEFAULT_WORKSPACE_HTTP_PORT,
    )


@then("workspace mcp-config should be valid YAML")
def then_workspace_mcp_config_valid(gherkin_context: GherkinContext) -> None:
    canonical = gherkin_context.payload["canonical"]
    assert_valid_mcp_config_yaml(canonical)
