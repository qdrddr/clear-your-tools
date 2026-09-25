"""Matrix tests for when cyt-mcp frontend entries are installed per scope/agent."""

from __future__ import annotations

from pathlib import Path

import pytest

from cyt.tools import cyt_mcp_setup
from tests.support.mcp_frontend_install_fixtures import (
    FrontendInstallMatrixCase,
    FrontendInstallTestbed,
    agent_mcp_backend_keys,
    agent_mcp_has_frontend,
    defs_has_backend,
    frontend_server_key,
    load_frontend_install_matrix,
)


@pytest.mark.parametrize(
    "case",
    load_frontend_install_matrix(),
    ids=lambda case: case.id,
)
def test_frontend_install_gate_matrix(
    case: FrontendInstallMatrixCase,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    testbed = FrontendInstallTestbed.create(
        tmp_path,
        agent=case.agent,
        scope=case.scope,
        monkeypatch=monkeypatch,
    )
    testbed.apply_case(case)
    testbed.run_setup()

    has_frontend = agent_mcp_has_frontend(
        testbed.agent_mcp_path,
        agent=case.agent,
        scope=case.scope,
    )
    if case.expect_should_install:
        assert has_frontend
    elif case.expect_frontend_unchanged:
        assert has_frontend
    else:
        assert not has_frontend

    assert defs_has_backend(testbed.defs_path, case.backend_server) is case.expect_backend_in_defs

    if case.expect_agent_frontend_only_after and case.agent != "codex":
        assert agent_mcp_backend_keys(testbed.agent_mcp_path, agent=case.agent) == set()
        if case.agent != "codex":
            payload = __import__("json").loads(testbed.agent_mcp_path.read_text(encoding="utf-8"))
            assert set(payload["mcpServers"]) == {frontend_server_key(case.scope)}


@pytest.mark.parametrize(
    "case",
    load_frontend_install_matrix(),
    ids=lambda case: f"has_migratable_{case.id}",
)
def test_has_migratable_matches_frontend_install_expectation(
    case: FrontendInstallMatrixCase,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    testbed = FrontendInstallTestbed.create(
        tmp_path,
        agent=case.agent,
        scope=case.scope,
        monkeypatch=monkeypatch,
    )
    testbed.apply_case(case)
    checker = (
        cyt_mcp_setup.has_migratable_user_mcp_backends
        if case.scope == "user"
        else cyt_mcp_setup.has_migratable_workspace_mcp_backends
    )
    assert checker(case.agent, testbed.cyt_scope) is case.expect_should_install
