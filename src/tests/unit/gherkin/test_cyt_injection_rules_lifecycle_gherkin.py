"""Gherkin steps for cyt-injection.mdc session lifecycle."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from pytest_bdd import given, scenarios, then, when

from cyt_client.rules_file import build_rules_mdc_placeholder, reset_rules_file_rel_path
from tests.support.cyt_injection_rules_lifecycle_fixtures import (
    before_submit_payload,
    build_hook_config,
    lifecycle_payload,
    load_lifecycle_scenario,
    materialize_lifecycle_workspace,
    patch_hook_environment,
    read_rules_text,
    reset_catalog_state,
    run_local_hook_inject,
    write_substantive_rules,
)
from tests.unit.gherkin.conftest import GherkinContext

FEATURES = (
    Path(__file__).resolve().parent / "features" / "cyt_injection_rules_lifecycle.feature"
)
scenarios(str(FEATURES))

pytestmark = pytest.mark.gherkin

_TOOLS_SCENARIO = load_lifecycle_scenario("before_submit_populates_tools")


@pytest.fixture(autouse=True)
def _isolate_lifecycle_gherkin_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr("cyt.hook.active_workspace.touch_active_workspace", lambda *_a, **_k: None)
    monkeypatch.setenv("CYT_HOOK_QUIET", "1")
    reset_rules_file_rel_path()
    reset_catalog_state()
    yield
    reset_rules_file_rel_path()
    reset_catalog_state()


def _workspace(gherkin_context: GherkinContext) -> Path:
    workspace = gherkin_context.payload.get("workspace")
    assert isinstance(workspace, Path)
    return workspace


def _run_cyt_client(payload: dict[str, object], *, hook_config: dict[str, object] | None = None) -> None:
    from cyt_client.cli import main

    event = payload.get("hook_event_name")
    if event == "beforeSubmitPrompt" and hook_config is not None:
        def _post_hook(_url: str, body: bytes, **_kwargs: object) -> tuple[int, bytes]:
            hook_payload = json.loads(body)
            assert isinstance(hook_payload, dict)
            return 200, run_local_hook_inject(hook_payload, hook_config)

        with patch(
            "cyt_client.cli._resolve_hook_url_for_submit",
            return_value="http://127.0.0.1:8834/hook/inject",
        ):
            with patch("cyt_client.cli.post_hook_inject", side_effect=_post_hook):
                with patch("sys.stdin.buffer.read", return_value=json.dumps(payload).encode()):
                    main()
        return

    with patch("cyt_client.cli.post_hook_inject") as post:
        with patch("sys.stdin.buffer.read", return_value=json.dumps(payload).encode()):
            main()
        post.assert_not_called()


@given("a Cursor workspace with no cyt-injection rules file")
def given_workspace_without_rules(tmp_path: Path, gherkin_context: GherkinContext) -> None:
    workspace = materialize_lifecycle_workspace(tmp_path)
    gherkin_context.payload = {"workspace": workspace}


@given("a Cursor workspace with substantive cyt-injection rules")
def given_workspace_with_substantive_rules(tmp_path: Path, gherkin_context: GherkinContext) -> None:
    workspace = materialize_lifecycle_workspace(tmp_path)
    write_substantive_rules(workspace)
    gherkin_context.payload = {"workspace": workspace}


@given("a workspace cyt-mcp catalog registered for hook injection")
def given_workspace_with_hook_catalog(
    tmp_path: Path,
    gherkin_context: GherkinContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = materialize_lifecycle_workspace(tmp_path)
    config = build_hook_config(workspace, db_path=tmp_path / "tiers-gherkin.db")
    patch_hook_environment(monkeypatch, workspace, config)
    gherkin_context.payload = {"workspace": workspace, "hook_config": config}


@when("cyt-client handles sessionStart for that workspace")
def when_session_start(gherkin_context: GherkinContext) -> None:
    workspace = _workspace(gherkin_context)
    _run_cyt_client(lifecycle_payload(workspace, "sessionStart"))


@when("cyt-client handles sessionEnd for that workspace")
def when_session_end(gherkin_context: GherkinContext) -> None:
    workspace = _workspace(gherkin_context)
    _run_cyt_client(lifecycle_payload(workspace, "sessionEnd"))


@when("cyt-client handles beforeSubmitPrompt with the lifecycle BM25 prompt")
def when_before_submit(gherkin_context: GherkinContext) -> None:
    workspace = _workspace(gherkin_context)
    hook_config = gherkin_context.payload.get("hook_config")
    assert isinstance(hook_config, dict)
    payload = before_submit_payload(workspace, str(_TOOLS_SCENARIO.raw["prompt"]))
    _run_cyt_client(payload, hook_config=hook_config)


@then("the cyt-injection rules file should be a session lifecycle placeholder")
def then_rules_placeholder(gherkin_context: GherkinContext) -> None:
    workspace = _workspace(gherkin_context)
    assert read_rules_text(workspace) == build_rules_mdc_placeholder()


@then("the cyt-injection rules file should contain pruned agent-tools")
def then_rules_contain_agent_tools(gherkin_context: GherkinContext) -> None:
    rules_text = read_rules_text(_workspace(gherkin_context))
    assert "<agent-tools" in rules_text
    assert "alwaysApply: true" in rules_text


@then("the cyt-injection rules file should include expected lifecycle tool names")
def then_rules_include_expected_tools(gherkin_context: GherkinContext) -> None:
    rules_text = read_rules_text(_workspace(gherkin_context))
    for tool_name in _TOOLS_SCENARIO.raw["expected_tool_names"]:
        assert tool_name in rules_text
