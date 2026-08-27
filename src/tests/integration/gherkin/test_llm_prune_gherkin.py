"""Gherkin steps for LLM pruning integration (wired to test_llm_prune_integration)."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from cyt.config import skills_selector_soft_budget, tools_selector_soft_budget
from tests.integration.test_llm_prune_integration import (
    DEFAULT_USER_PROMPT,
    ScenarioMode,
    _fixtures_available,
    _integration_config,
    _llm_credentials_available,
    _parse_agent,
    run_hook_daemon_scenario,
    run_llm_prune_scenario,
)
from tests.unit.gherkin.conftest import GherkinContext

FEATURES = Path(__file__).resolve().parent / "features" / "llm_prune.feature"
scenarios(str(FEATURES))

pytestmark = [pytest.mark.integration, pytest.mark.gherkin]


def _parse_scenario_mode(raw: str) -> ScenarioMode:
    normalized = raw.strip().lower()
    if normalized == "tools":
        return "tools"
    if normalized == "skills":
        return "skills"
    if normalized == "combined":
        return "combined"
    if normalized == "real":
        return "real"
    msg = f"unsupported pruning mode: {raw!r}"
    raise ValueError(msg)


@given("default pruning fixtures are available")
def given_fixtures_available() -> None:
    if not _fixtures_available():
        pytest.skip("default ~/.config/cyt fixtures are not present on this machine")


@given("LLM pruning credentials are configured")
def given_llm_credentials(gherkin_context: GherkinContext) -> None:
    gherkin_context.config = _integration_config(mode="tools")
    if not _llm_credentials_available(gherkin_context.config):
        pytest.skip("pruning LLM credentials are not configured")


@given(parsers.parse('pruning mode is "{mode}"'))
def given_pruning_mode(
    mode: str,
    gherkin_context: GherkinContext,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    scenario_mode = _parse_scenario_mode(mode)
    agent = _parse_agent(cast(str, request.config.getoption("--agent")))
    gherkin_context.mode = scenario_mode
    gherkin_context.agent = agent
    gherkin_context.rule_path = request.config.getoption("--rule")
    gherkin_context.tmp_path = tmp_path
    gherkin_context.config = _integration_config(
        mode=scenario_mode,
        stats_db=str(tmp_path / "stats.db"),
    )


@when("the LLM selector scenario runs")
def when_selector_runs(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.mode is not None
    scenario_mode = gherkin_context.mode
    gherkin_context.selector_trace = run_llm_prune_scenario(
        scenario_mode,
        prompt=DEFAULT_USER_PROMPT,
        config=gherkin_context.config,
        agent=gherkin_context.agent,
    )
    trace = gherkin_context.selector_trace
    assert trace is not None
    assert isinstance(trace.selected_ids, set)
    assert isinstance(trace.selected_scores, dict)
    if trace.tools_selector is not None:
        assert trace.tools_selector.bulk_plan.domain == "tools"
        assert trace.tools_selector.bulk_plan.soft_budget_total == tools_selector_soft_budget(
            gherkin_context.config,
        )
    if trace.mode == "skills" and trace.skills_selector is not None:
        assert trace.selected_scores == trace.skills_selector.selected_scores
        assert trace.skills_selector.bulk_plan.domain == "skills"
        assert trace.skills_selector.bulk_plan.soft_budget_total == skills_selector_soft_budget(
            gherkin_context.config,
        )


@when("the hook daemon scenario runs")
def when_daemon_runs(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.mode is not None
    scenario_mode = gherkin_context.mode
    assert gherkin_context.selector_trace is not None
    gherkin_context.daemon_trace = run_hook_daemon_scenario(
        scenario_mode,
        prompt=DEFAULT_USER_PROMPT,
        config=gherkin_context.config,
        agent=gherkin_context.agent,
        rule_path=gherkin_context.rule_path,
        selector_trace=gherkin_context.selector_trace,
    )


@then("the enriched payload identifies the configured agent")
def then_agent_identified(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.selector_trace is not None
    assert gherkin_context.daemon_trace is not None
    assert gherkin_context.selector_trace.enriched_hook_payload.get("cyt_agent") == (
        gherkin_context.agent
    )
    assert gherkin_context.daemon_trace.enriched_hook_payload.get("cyt_agent") == (
        gherkin_context.agent
    )


@then("the hook daemon reports a successful injection")
def then_successful_injection(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.daemon_trace is not None
    assert gherkin_context.daemon_trace.outcome in {
        "user_prompt_injected",
        "user_prompt_tools_injected",
        "user_prompt_skills_injected",
    }
    assert gherkin_context.daemon_trace.stdout_text.strip()
    if gherkin_context.rule_path and gherkin_context.agent == "cursor":
        assert gherkin_context.daemon_trace.rules_file is not None
        assert gherkin_context.daemon_trace.rules_file.is_file()
