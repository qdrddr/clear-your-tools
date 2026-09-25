"""Integration tests for user-global and workspace MCP migration frontend-only cleanup."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import cyt.hook.install_scope as install_scope
from cyt.hook import setup_wizard as hook_setup
from cyt.hook.cli_invocation import HookCliInvocation
from cyt.hook.install_scope import CytInstallScope
from cyt.hook.workspace_resolution import CYT_WORKSPACE_ENV
from cyt.tools import cyt_mcp_setup
from cyt_client.mcp_entry import CYT_MCP_USER_SERVER_KEY, CYT_MCP_WORKSPACE_SERVER_KEY
from tests.support.mcp_frontend_install_fixtures import (
    FrontendInstallTestbed,
    agent_mcp_has_frontend,
    load_frontend_install_matrix,
)
from tests.support.mcp_migration_fixtures import (
    load_migration_scenario,
    prepare_stale_workspace_migration_tree,
    write_stale_user_cursor_mcp,
)

pytestmark = pytest.mark.integration


def _prepare_cyt_checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cyt_repo = tmp_path / "cyt-checkout"
    consumer = tmp_path / "consumer"
    for repo in (cyt_repo, consumer):
        repo.mkdir()
        (repo / ".git").mkdir()
    monkeypatch.chdir(cyt_repo)
    monkeypatch.setattr(
        "cyt.hook.workspace_resolution.cyt_package_git_root",
        lambda: cyt_repo.resolve(),
    )
    return consumer.resolve()


def test_setup_cyt_mcp_rerun_strips_stale_user_backends(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = load_migration_scenario("user")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    source = tmp_path / "cursor" / "mcp.json"
    target_dir = tmp_path / "cyt_mcp"
    aggregator_path = tmp_path / "mcp-config.yaml"
    write_stale_user_cursor_mcp(source, repo_root=repo_root)
    monkeypatch.setitem(cyt_mcp_setup._AGENT_SOURCE_PATHS, "cursor", source)
    monkeypatch.setattr(cyt_mcp_setup, "DEFAULT_MCP_DIR", target_dir)
    monkeypatch.setattr(cyt_mcp_setup, "DEFAULT_MCP_CONFIG_PATH", aggregator_path)
    monkeypatch.setattr(cyt_mcp_setup, "DEFAULT_AGGREGATOR_PATH", aggregator_path)

    cyt_mcp_setup.setup_cyt_mcp_for_agent(
        "cursor",
        invocation=HookCliInvocation(mode="dev", repo_root=repo_root),
        transport="stdio",
    )

    agent_payload = json.loads(source.read_text(encoding="utf-8"))
    backend_payload = json.loads((target_dir / "cursor.json").read_text(encoding="utf-8"))
    assert set(agent_payload["mcpServers"]) == set(scenario["expected_agent_mcp_keys"])
    assert set(backend_payload["mcpServers"]) == set(scenario["expected_backend_keys"])


def test_hook_setup_rerun_strips_stale_user_backends(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scenario = load_migration_scenario("user")
    consumer = _prepare_cyt_checkout(tmp_path, monkeypatch)
    repo_root = Path.cwd()
    cursor_hooks_path = tmp_path / "cursor" / "hooks.json"
    mcp_source = tmp_path / "cursor" / "mcp.json"
    mcp_target_dir = tmp_path / "cyt_mcp"
    aggregator_path = tmp_path / "mcp-config.yaml"
    config_path = tmp_path / "config.yaml"
    mcp_source.parent.mkdir(parents=True, exist_ok=True)
    write_stale_user_cursor_mcp(mcp_source, repo_root=repo_root)
    project_mcp = consumer / ".cursor" / "mcp.json"
    project_mcp.parent.mkdir(parents=True)
    project_mcp.write_text(json.dumps({"mcpServers": {}}) + "\n", encoding="utf-8")
    monkeypatch.setattr(hook_setup, "CURSOR_HOOKS_PATH", cursor_hooks_path)
    monkeypatch.setitem(cyt_mcp_setup._AGENT_SOURCE_PATHS, "cursor", mcp_source)
    monkeypatch.setattr(cyt_mcp_setup, "DEFAULT_MCP_DIR", mcp_target_dir)
    monkeypatch.setattr(cyt_mcp_setup, "DEFAULT_MCP_CONFIG_PATH", aggregator_path)
    monkeypatch.setattr(cyt_mcp_setup, "DEFAULT_AGGREGATOR_PATH", aggregator_path)
    monkeypatch.setattr(hook_setup, "_ensure_hook_credentials", lambda _config: None)
    monkeypatch.setattr(hook_setup.sys.stdin, "isatty", lambda: True)

    def capture_yes_no(text: str, *args: object, **kwargs: object) -> bool:
        if "Migrate user-global MCP backends and install cyt-mcp-usr?" in text:
            return True
        if "Migrate project MCP backends and install cyt-mcp-ws?" in text:
            return False
        if "choose action" in text.lower():
            return True
        raise AssertionError(f"unexpected yes/no prompt: {text!r}")

    monkeypatch.setattr(hook_setup, "_prompt_yes_no", capture_yes_no)
    monkeypatch.setattr(hook_setup, "_prompt_choice", lambda text, *a, **k: "update")

    with (
        patch(
            "cyt.hook.setup_wizard.load_config",
            return_value={"skills": {"enabled": False}},
        ),
        patch("cyt.config.save_user_config", return_value=True),
        patch("cyt.config.sync_config_in_place"),
        patch("cyt.hook.daemon.daemon_start"),
        patch(
            "cyt.hook.setup_wizard.resolve_setup_config_path",
            return_value=config_path,
        ),
    ):
        hook_setup.run_hook_setup(
            config_path=config_path,
            agents=["cursor"],
            prevent_hallucinations=True,
            workspace=consumer,
        )

    agent_payload = json.loads(mcp_source.read_text(encoding="utf-8"))
    backend_payload = json.loads((mcp_target_dir / "cursor.json").read_text(encoding="utf-8"))
    assert set(agent_payload["mcpServers"]) == {CYT_MCP_USER_SERVER_KEY}
    assert set(backend_payload["mcpServers"]) == set(scenario["expected_backend_keys"])
    assert scenario["backend_server"] not in agent_payload["mcpServers"]
    _ = capsys.readouterr()


def test_setup_cyt_mcp_rerun_strips_stale_workspace_backends(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = load_migration_scenario("workspace")
    repo_root = tmp_path / "repo"
    workspace_root = tmp_path / "workspace"
    repo_root.mkdir()
    project_mcp, backend_defs = prepare_stale_workspace_migration_tree(
        workspace_root,
        repo_root=repo_root,
    )
    scope = CytInstallScope(workspace_root=workspace_root.resolve())
    monkeypatch.setattr(
        install_scope.CytInstallScope,
        "from_cwd",
        classmethod(lambda cls, *, cwd=None: scope),
    )

    cyt_mcp_setup.setup_cyt_mcp_for_agent(
        "cursor",
        invocation=HookCliInvocation(mode="dev", repo_root=repo_root),
        transport="stdio",
        configure_user=False,
        configure_workspace=True,
        scope=scope,
    )

    project_payload = json.loads(project_mcp.read_text(encoding="utf-8"))
    backend_payload = json.loads(backend_defs.read_text(encoding="utf-8"))
    assert set(project_payload["mcpServers"]) == set(scenario["expected_agent_mcp_keys"])
    assert set(backend_payload["mcpServers"]) == set(scenario["expected_backend_keys"])
    assert scenario["backend_server"] not in project_payload["mcpServers"]


def test_hook_setup_rerun_strips_stale_workspace_backends(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scenario = load_migration_scenario("workspace")
    cyt_repo = tmp_path / "clear-your-tools"
    cyt_repo.mkdir()
    (cyt_repo / ".git").mkdir()
    repo_root = cyt_repo.resolve()
    project_mcp, backend_defs = prepare_stale_workspace_migration_tree(
        cyt_repo,
        repo_root=repo_root,
    )
    cursor_hooks_path = tmp_path / "cursor" / "hooks.json"
    mcp_source = tmp_path / "cursor" / "mcp.json"
    mcp_target_dir = tmp_path / "cyt_mcp"
    aggregator_path = tmp_path / "mcp-config.yaml"
    config_path = tmp_path / "config.yaml"
    mcp_source.parent.mkdir(parents=True, exist_ok=True)
    mcp_source.write_text(json.dumps({"mcpServers": {}}) + "\n", encoding="utf-8")
    monkeypatch.chdir(cyt_repo)
    monkeypatch.setenv(CYT_WORKSPACE_ENV, str(cyt_repo))
    monkeypatch.setattr(
        "cyt.hook.workspace_resolution.cyt_package_git_root",
        lambda: cyt_repo.resolve(),
    )
    monkeypatch.setattr(hook_setup, "CURSOR_HOOKS_PATH", cursor_hooks_path)
    monkeypatch.setitem(cyt_mcp_setup._AGENT_SOURCE_PATHS, "cursor", mcp_source)
    monkeypatch.setattr(cyt_mcp_setup, "DEFAULT_MCP_DIR", mcp_target_dir)
    monkeypatch.setattr(cyt_mcp_setup, "DEFAULT_MCP_CONFIG_PATH", aggregator_path)
    monkeypatch.setattr(cyt_mcp_setup, "DEFAULT_AGGREGATOR_PATH", aggregator_path)
    monkeypatch.setattr(hook_setup, "_ensure_hook_credentials", lambda _config: None)
    monkeypatch.setattr(hook_setup.sys.stdin, "isatty", lambda: True)

    def capture_yes_no(text: str, *args: object, **kwargs: object) -> bool:
        if "Migrate user-global MCP backends and install cyt-mcp-usr?" in text:
            return False
        if "Migrate project MCP backends and install cyt-mcp-ws?" in text:
            return True
        if "choose action" in text.lower():
            return True
        raise AssertionError(f"unexpected yes/no prompt: {text!r}")

    monkeypatch.setattr(hook_setup, "_prompt_yes_no", capture_yes_no)
    monkeypatch.setattr(hook_setup, "_prompt_choice", lambda text, *a, **k: "update")

    with (
        patch(
            "cyt.hook.setup_wizard.load_config",
            return_value={"skills": {"enabled": False}},
        ),
        patch("cyt.config.save_user_config", return_value=True),
        patch("cyt.config.sync_config_in_place"),
        patch("cyt.hook.daemon.daemon_start"),
        patch(
            "cyt.hook.setup_wizard.resolve_setup_config_path",
            return_value=config_path,
        ),
    ):
        hook_setup.run_hook_setup(
            config_path=config_path,
            agents=["cursor"],
            prevent_hallucinations=True,
            workspace=cyt_repo,
        )

    project_payload = json.loads(project_mcp.read_text(encoding="utf-8"))
    backend_payload = json.loads(backend_defs.read_text(encoding="utf-8"))
    assert set(project_payload["mcpServers"]) == {CYT_MCP_WORKSPACE_SERVER_KEY}
    assert set(backend_payload["mcpServers"]) == set(scenario["expected_backend_keys"])
    assert scenario["backend_server"] not in project_payload["mcpServers"]
    _ = capsys.readouterr()


@pytest.mark.parametrize(
    "case",
    [
        item
        for item in load_frontend_install_matrix()
        if item.agent == "cursor" and item.id.endswith("add_frontend_when_agent_has_backend")
    ],
    ids=lambda case: case.id,
)
def test_frontend_install_gate_integration_sample(
    case: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.support.mcp_frontend_install_fixtures import FrontendInstallMatrixCase

    matrix_case = case
    assert isinstance(matrix_case, FrontendInstallMatrixCase)
    testbed = FrontendInstallTestbed.create(
        tmp_path,
        agent=matrix_case.agent,
        scope=matrix_case.scope,
        monkeypatch=monkeypatch,
    )
    testbed.apply_case(matrix_case)
    testbed.run_setup()
    assert agent_mcp_has_frontend(
        testbed.agent_mcp_path,
        agent=matrix_case.agent,
        scope=matrix_case.scope,
    )
