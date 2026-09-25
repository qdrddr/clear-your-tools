"""Gherkin steps for cyt-mcp frontend install gate matrix."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from cyt.tools import cyt_mcp_setup
from tests.support.mcp_frontend_install_fixtures import (
    FrontendInstallMatrixCase,
    FrontendInstallTestbed,
    InstallScopeKind,
    agent_mcp_has_frontend,
    frontend_server_key,
    load_frontend_install_matrix,
)
from tests.unit.gherkin.conftest import GherkinContext
from tests.unit.gherkin.test_cyt_dev_injection_gherkin import given_dev_mode

FEATURES = Path(__file__).resolve().parent / "features" / "mcp_frontend_install_gate.feature"
scenarios(str(FEATURES))

pytestmark = pytest.mark.gherkin


def _case_for(
    agent: str,
    scope: str,
    agent_mcp_state: str,
    defs_state: str,
) -> FrontendInstallMatrixCase:
    for case in load_frontend_install_matrix():
        if (
            case.agent == agent
            and case.scope == scope
            and case.agent_mcp_state == agent_mcp_state
            and case.defs_state == defs_state
        ):
            return case
    raise AssertionError(
        f"no matrix case for agent={agent!r} scope={scope!r} "
        f"agent_mcp_state={agent_mcp_state!r} defs_state={defs_state!r}",
    )


def _given_install_state(
    gherkin_context: GherkinContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    scope: str,
    agent: str,
    agent_mcp_state: str,
    defs_state: str,
) -> None:
    case = _case_for(agent, scope, agent_mcp_state, defs_state)
    assert scope in {"user", "workspace"}
    scope_kind: InstallScopeKind = "user" if scope == "user" else "workspace"
    testbed = FrontendInstallTestbed.create(
        tmp_path,
        agent=agent,
        scope=scope_kind,
        monkeypatch=monkeypatch,
    )
    testbed.apply_case(case)
    gherkin_context.payload["testbed"] = testbed
    gherkin_context.payload["case"] = case


@given("cyt hook development mode for the current repo")
def given_dev_mode_for_gate(gherkin_context: GherkinContext) -> None:
    given_dev_mode(gherkin_context)


@given(
    parsers.parse(
        'user-scoped {agent} MCP install state is "{agent_mcp_state}" with defs "{defs_state}"',
    ),
)
def given_user_install_state(
    gherkin_context: GherkinContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    agent: str,
    agent_mcp_state: str,
    defs_state: str,
) -> None:
    _given_install_state(
        gherkin_context,
        tmp_path,
        monkeypatch,
        scope="user",
        agent=agent,
        agent_mcp_state=agent_mcp_state,
        defs_state=defs_state,
    )


@given(
    parsers.parse(
        'workspace-scoped {agent} MCP install state is "{agent_mcp_state}" with defs "{defs_state}"',
    ),
)
def given_workspace_install_state(
    gherkin_context: GherkinContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    agent: str,
    agent_mcp_state: str,
    defs_state: str,
) -> None:
    _given_install_state(
        gherkin_context,
        tmp_path,
        monkeypatch,
        scope="workspace",
        agent=agent,
        agent_mcp_state=agent_mcp_state,
        defs_state=defs_state,
    )


@when(parsers.parse("cyt-mcp user setup runs for {agent}"))
def when_user_setup(gherkin_context: GherkinContext, agent: str) -> None:
    testbed: FrontendInstallTestbed = gherkin_context.payload["testbed"]
    assert testbed.agent == agent
    cyt_mcp_setup.setup_cyt_mcp_user_for_agent(
        agent,
        transport="stdio",
        scope=testbed.cyt_scope,
    )


@when(parsers.parse("cyt-mcp workspace setup runs for {agent}"))
def when_workspace_setup(gherkin_context: GherkinContext, agent: str) -> None:
    testbed: FrontendInstallTestbed = gherkin_context.payload["testbed"]
    assert testbed.agent == agent
    cyt_mcp_setup.setup_cyt_mcp_workspace_for_agent(
        agent,
        testbed.cyt_scope,
        transport="stdio",
    )


def _assert_install_outcome(
    gherkin_context: GherkinContext,
    agent: str,
    expected: str,
) -> None:
    testbed: FrontendInstallTestbed = gherkin_context.payload["testbed"]
    case: FrontendInstallMatrixCase = gherkin_context.payload["case"]
    assert testbed.agent == agent
    expect_install = expected.strip().lower() == "true"
    assert case.expect_should_install is expect_install

    has_frontend = agent_mcp_has_frontend(
        testbed.agent_mcp_path,
        agent=agent,
        scope=case.scope,
    )
    if expect_install:
        assert has_frontend
        if case.agent != "codex" and case.expect_agent_frontend_only_after:
            payload = json.loads(testbed.agent_mcp_path.read_text(encoding="utf-8"))
            assert set(payload["mcpServers"]) == {frontend_server_key(case.scope)}
    elif case.expect_frontend_unchanged:
        assert has_frontend
    elif case.agent != "codex" and testbed.agent_mcp_path.is_file():
        payload = json.loads(testbed.agent_mcp_path.read_text(encoding="utf-8"))
        key = frontend_server_key(case.scope)
        assert key not in payload.get("mcpServers", {})


@then(parsers.parse('user-scoped {agent} should_install cyt-mcp-usr is "{expected}"'))
def then_user_should_install(gherkin_context: GherkinContext, agent: str, expected: str) -> None:
    _assert_install_outcome(gherkin_context, agent, expected)


@then(parsers.parse('workspace-scoped {agent} should_install cyt-mcp-ws is "{expected}"'))
def then_workspace_should_install(
    gherkin_context: GherkinContext,
    agent: str,
    expected: str,
) -> None:
    _assert_install_outcome(gherkin_context, agent, expected)
