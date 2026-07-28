"""Gherkin steps for agent harness detection (wired to test_cyt_client_agent)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from cyt_client.agent import (
    CLAUDE_CODE_ENTRYPOINT_ENV,
    CLAUDE_PROJECT_DIR_ENV,
    CLAUDECODE_ENV,
    CODEX_HOME_ENV,
    CURSOR_VERSION_ENV,
    CYT_LAUNCH_AGENT_ENV,
    infer_harness_agent,
)
from tests.gherkin.conftest import GherkinContext

FEATURES = Path(__file__).resolve().parent / "features" / "harness_detection.feature"
scenarios(str(FEATURES))

pytestmark = pytest.mark.gherkin

_HARNESS_ENV_VARS = (
    CODEX_HOME_ENV,
    CURSOR_VERSION_ENV,
    CLAUDE_PROJECT_DIR_ENV,
    CLAUDECODE_ENV,
    CLAUDE_CODE_ENTRYPOINT_ENV,
    CYT_LAUNCH_AGENT_ENV,
)


@given("the harness environment is cleared")
def given_harness_env_cleared(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _HARNESS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@given(parsers.parse('environment variable "{name}" is "{value}"'))
def given_env_var(name: str, value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    env_map = {
        "CURSOR_VERSION": CURSOR_VERSION_ENV,
        "CYT_LAUNCH_AGENT": CYT_LAUNCH_AGENT_ENV,
        "CODEX_HOME": CODEX_HOME_ENV,
    }
    monkeypatch.setenv(env_map[name], value)


@when("harness agent is inferred from an empty payload")
def when_infer_empty(gherkin_context: GherkinContext) -> None:
    gherkin_context.payload = {}
    gherkin_context.payload["inferred_agent"] = infer_harness_agent({})


@when("harness agent is inferred from a beforeSubmitPrompt payload")
def when_infer_before_submit(gherkin_context: GherkinContext) -> None:
    payload: dict[str, Any] = {"hook_event_name": "beforeSubmitPrompt", "prompt": "hello"}
    gherkin_context.payload = payload
    gherkin_context.payload["inferred_agent"] = infer_harness_agent(payload)


@then(parsers.parse('harness agent should be "{expected}"'))
def then_harness_agent(expected: str, gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload["inferred_agent"] == expected
