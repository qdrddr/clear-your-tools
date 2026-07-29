"""Gherkin steps for tools hook injection (wired to test_tools_hook_inject)."""

from __future__ import annotations

import json
import sys
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from pytest_bdd import given, scenarios, then, when

from cyt.proxy.anthropic import PruneResult
from cyt.skills import cli as skills_cli
from tests.support.paths import FIXTURES_DIR
from tests.unit.gherkin.conftest import GherkinContext

FEATURES = Path(__file__).resolve().parent / "features" / "tools_hook_inject.feature"
scenarios(str(FEATURES))

pytestmark = pytest.mark.gherkin


@pytest.fixture(autouse=True)
def _quiet_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CYT_HOOK_QUIET", "1")


def _hook_payload(prompt: str = "read a file") -> dict[str, Any]:
    return {
        "hook_event_name": "UserPromptSubmit",
        "prompt": prompt,
        "cwd": "/tmp/project",
        "model": "claude-sonnet-4-20250514",
    }


def _tools_hook_config(root: Path, definitions: Path) -> dict[str, Any]:
    return {
        "skills": {"enabled": False},
        "pruning": {
            "inject_via": "hook",
            "tools": {
                "hook": {
                    "tools_from": "definitions",
                    "mcp_definitions_file": str(definitions),
                },
                "sequence": ["bm25"],
            },
        },
        "stats": {"database": {"path": str(root / "stats.db")}},
    }


@given("tools hook pruning is disabled")
def given_tools_disabled(gherkin_context: GherkinContext) -> None:
    gherkin_context.config = {
        "skills": {"enabled": False},
        "pruning": {
            "inject_via": "hook",
            "tools": {"enabled": False},
        },
    }


@given("a tools hook config with MCP definitions fixture")
def given_tools_hook_config(gherkin_context: GherkinContext, tmp_path: Path) -> None:
    fixture = FIXTURES_DIR / "mcp_definitions_sample.json"
    gherkin_context.config = _tools_hook_config(tmp_path, fixture)
    gherkin_context.payload = {"definitions_fixture": fixture}


@given("coordinated prune returns one pruned tool")
def given_prune_result(gherkin_context: GherkinContext) -> None:
    fixture = gherkin_context.payload["definitions_fixture"]
    catalog = json.loads(fixture.read_text(encoding="utf-8"))["tools"]
    pruned = [catalog[0]]
    gherkin_context.payload["pruned"] = pruned
    gherkin_context.payload["catalog"] = catalog


@when("the tools hook runs with a user prompt payload")
def when_hook_runs_disabled(gherkin_context: GherkinContext) -> None:
    result = skills_cli.run_hook_payload(_hook_payload(), gherkin_context.config)
    gherkin_context.payload["result"] = result


@when("the tools hook CLI runs")
def when_hook_cli_runs(gherkin_context: GherkinContext, tmp_path: Path) -> None:
    config = gherkin_context.config
    pruned = gherkin_context.payload["pruned"]
    catalog = gherkin_context.payload["catalog"]
    prune_result = PruneResult(
        tools=pruned,
        status="applied",
        query="read a file",
        tools_in=2,
        mcp_tools_in=2,
        tools_out=1,
        error=None,
        tokens_in=100,
        tokens_out=40,
    )
    with (
        patch.object(skills_cli, "load_config", return_value=config),
        patch(
            "cyt.tools.hook.run_hook_coordinated_prune",
            return_value=(prune_result, None, catalog, {"root": prune_result}, {}),
        ),
        patch("cyt.tools.hook.record_tools_hook_injection"),
        patch.object(sys, "stdin", StringIO(json.dumps(_hook_payload()))),
    ):
        stdout = StringIO()
        with patch.object(sys, "stdout", stdout):
            skills_cli.run(debug=False)
        gherkin_context.stdout = stdout.getvalue()


@then("hook stdout should be empty")
def then_stdout_empty(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload["result"].stdout_text == ""


@then("hook outcome should be skipped_inject_via_proxy")
def then_outcome_skipped(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload["result"].outcome == "skipped_inject_via_proxy"


@then("hook output should include agent-tools context")
def then_agent_tools(gherkin_context: GherkinContext) -> None:
    output = json.loads(gherkin_context.stdout)
    context = output["hookSpecificOutput"]["additionalContext"]
    assert output["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert "<agent-tools" in context
    assert " path='/tmp/project'" in context
    gherkin_context.payload["context"] = context


@then("hook output should reference read_file tool")
def then_read_file(gherkin_context: GherkinContext) -> None:
    assert "mcp__filesystem__read_file" in gherkin_context.payload["context"]
