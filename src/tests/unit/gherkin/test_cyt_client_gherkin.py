"""Gherkin steps for cyt-client hook forwarding (wired to test_cyt_client)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from cyt_client.rules_file import build_rules_mdc_placeholder, reset_rules_file_rel_path
from tests.unit.gherkin.conftest import GherkinContext

FEATURES = Path(__file__).resolve().parent / "features" / "cyt_client.feature"
scenarios(str(FEATURES))

pytestmark = pytest.mark.gherkin


@given("a UserPromptSubmit hook payload")
def given_user_prompt_payload(gherkin_context: GherkinContext) -> None:
    gherkin_context.payload = {
        "hook_event_name": "UserPromptSubmit",
        "prompt": "hello",
        "cwd": "/tmp/isolated-project",
    }


@given("a beforeSubmitPrompt hook payload with a workspace")
def given_before_submit_payload(gherkin_context: GherkinContext) -> None:
    tmp = tempfile.TemporaryDirectory()
    workspace = Path(tmp.name) / "project"
    workspace.mkdir()
    gherkin_context.payload = {
        "_tmp": tmp,
        "_workspace": workspace,
        "hook_event_name": "beforeSubmitPrompt",
        "prompt": "hello",
        "conversation_id": "conv-1",
        "workspace_roots": [str(workspace)],
    }


@given("no hook server URL can be resolved")
def given_no_hook_server(gherkin_context: GherkinContext) -> None:
    gherkin_context.payload["_hook_url"] = None


@given("the hook server returns injected context")
def given_hook_returns_context(gherkin_context: GherkinContext) -> None:
    gherkin_context.payload["_hook_url"] = "http://127.0.0.1:8834/hook/inject"


@given("a Cursor workspace with a stale rules file")
def given_stale_rules_workspace(gherkin_context: GherkinContext) -> None:
    tmp = tempfile.TemporaryDirectory()
    workspace = Path(tmp.name) / "project"
    workspace.mkdir()
    rules_path = workspace / ".cursor" / "rules" / "cyt-injection.mdc"
    rules_path.parent.mkdir(parents=True)
    rules_path.write_text("stale pruned injection", encoding="utf-8")
    gherkin_context.payload = {
        "_tmp": tmp,
        "_workspace": workspace,
        "_rules_path": rules_path,
    }


@given(parsers.parse("a {event} hook payload for that workspace"))
def given_session_lifecycle_payload(event: str, gherkin_context: GherkinContext) -> None:
    workspace = gherkin_context.payload["_workspace"]
    gherkin_context.payload["hook_event_name"] = event
    gherkin_context.payload["workspace_roots"] = [str(workspace)]


@when("cyt-client runs for session lifecycle")
def when_cyt_client_session_lifecycle(
    gherkin_context: GherkinContext,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from cyt_client.cli import main

    payload = gherkin_context.payload
    stdin_payload = {key: value for key, value in payload.items() if not key.startswith("_")}
    with patch("cyt_client.cli.post_hook_inject") as post:
        with patch("sys.stdin.buffer.read", return_value=json.dumps(stdin_payload).encode()):
            main()
        gherkin_context.payload["_post_hook_inject"] = post

    captured = capsys.readouterr()
    gherkin_context.stdout = captured.out
    gherkin_context.stderr = captured.err


@when("cyt-client runs without verbose logging")
def when_cyt_client_runs(
    gherkin_context: GherkinContext,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from cyt_client.cli import main

    payload = gherkin_context.payload
    hook_url = payload.get("_hook_url", "http://127.0.0.1:8834/hook/inject")
    stdin_payload = {key: value for key, value in payload.items() if not key.startswith("_")}

    if payload.get("hook_event_name") == "beforeSubmitPrompt":
        inject_response = json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": "<agent-skills>skill text</agent-skills>",
                },
            },
        ).encode()
        with patch("cyt_client.cli.resolve_hook_url", return_value=hook_url):
            with patch("cyt_client.cli.post_hook_inject", return_value=(200, inject_response)):
                with patch(
                    "sys.stdin.buffer.read",
                    return_value=json.dumps(stdin_payload).encode(),
                ):
                    main()
    else:
        inject_body = b'{"hookSpecificOutput":{"additionalContext":"x"}}'
        with patch("cyt_client.cli.resolve_hook_url", return_value=hook_url):
            if hook_url is None:
                with patch(
                    "sys.stdin.buffer.read",
                    return_value=json.dumps(stdin_payload).encode(),
                ):
                    main()
            else:
                with patch("cyt_client.cli.post_hook_inject", return_value=(200, inject_body)):
                    with patch(
                        "sys.stdin.buffer.read",
                        return_value=json.dumps(stdin_payload).encode(),
                    ):
                        main()

    captured = capsys.readouterr()
    gherkin_context.stdout = captured.out
    gherkin_context.stderr = captured.err


@then("cyt-client stdout should be empty")
def then_stdout_empty(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.stdout == ""


@then("cyt-client stderr should be empty")
def then_stderr_empty(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.stderr == ""


@then("cyt-client stdout should contain hook injection output")
def then_stdout_has_injection(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.stdout == '{"hookSpecificOutput":{"additionalContext":"x"}}'


@then("cyt-client stdout should be Cursor beforeSubmitPrompt JSON")
def then_stdout_before_submit(gherkin_context: GherkinContext) -> None:
    assert json.loads(gherkin_context.stdout) == {
        "continue": True,
        "additional_context": "<agent-skills>skill text</agent-skills>",
    }


@then("cyt-client stdout should be Cursor continue JSON")
def then_stdout_continue(gherkin_context: GherkinContext) -> None:
    assert json.loads(gherkin_context.stdout) == {"continue": True}


@then("the Cursor rules file should be a session lifecycle placeholder")
def then_rules_file_session_placeholder(gherkin_context: GherkinContext) -> None:
    rules_path = gherkin_context.payload["_rules_path"]
    assert rules_path.is_file()
    assert rules_path.read_text(encoding="utf-8") == build_rules_mdc_placeholder()


@then("the hook server should not have been called")
def then_hook_not_called(gherkin_context: GherkinContext) -> None:
    try:
        post = gherkin_context.payload["_post_hook_inject"]
        post.assert_not_called()
    finally:
        reset_rules_file_rel_path()
        tmp = gherkin_context.payload.get("_tmp")
        if tmp is not None:
            tmp.cleanup()


@then("a Cursor rules file should contain the injected context")
def then_rules_file_has_context(gherkin_context: GherkinContext) -> None:
    try:
        workspace = gherkin_context.payload["_workspace"]
        rules_path = workspace / ".cursor" / "rules" / "cyt-injection.mdc"
        assert rules_path.is_file()
        text = rules_path.read_text(encoding="utf-8")
        assert "alwaysApply: true" in text
        assert "<agent-skills>skill text</agent-skills>" in text
    finally:
        reset_rules_file_rel_path()
        tmp = gherkin_context.payload.get("_tmp")
        if tmp is not None:
            tmp.cleanup()
