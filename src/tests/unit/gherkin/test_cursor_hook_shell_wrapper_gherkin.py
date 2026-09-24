"""Gherkin steps for Cursor hook shell wrapper (fish/bash) compatibility."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from pytest_bdd import given, scenarios, then, when

from cyt.hook import setup_wizard as hook_setup
from cyt.hook.cli_invocation import HookCliInvocation, repo_root_from_proxy_cli_script
from cyt_client.rules_file import (
    RULES_REL_PATH,
    is_rules_placeholder_body,
    read_cursor_rules_injection,
)
from tests.support.cursor_hook_shell_wrapper_fixtures import (
    FISH_BREAKING_INLINE_PREFIX,
    SESSION_START_PAYLOAD,
    cursor_dev_client_wrapper_command,
    fish_available,
    load_legacy_fish_breaking_hooks,
    run_hook_wrapper_via_fish,
    wrapper_suffix,
)
from tests.unit.gherkin.conftest import GherkinContext

FEATURES = Path(__file__).resolve().parent / "features" / "cursor_hook_shell_wrapper.feature"
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
def when_build_wrapper_entries(gherkin_context: GherkinContext) -> None:
    invocation = gherkin_context.payload["invocation"]
    entries = hook_setup.cursor_hook_entries(agent="cursor", invocation=invocation)
    gherkin_context.payload["entries"] = entries


@then("cursor hook commands should use shell wrapper scripts")
def then_commands_use_wrappers(gherkin_context: GherkinContext) -> None:
    entries = gherkin_context.payload["entries"]
    commands = [
        entries["before_submit"]["command"],
        entries["session_end"]["command"],
        entries["pre_tool"]["command"],
        *[entry["command"] for entry in entries["session_start"]],
    ]
    for command in commands:
        assert str(command).endswith(wrapper_suffix()) or str(command).endswith(
            "cyt-hook-daemon-start-dev.cmd"
            if sys.platform == "win32"
            else "cyt-hook-daemon-start-dev.sh",
        )


@then("cursor hook commands should not use fish-breaking inline env prefixes")
def then_commands_avoid_inline_prefix(gherkin_context: GherkinContext) -> None:
    entries = gherkin_context.payload["entries"]
    for key in ("before_submit", "session_end", "pre_tool", "pre_compact"):
        assert FISH_BREAKING_INLINE_PREFIX not in entries[key]["command"]
    for entry in entries["session_start"]:
        assert FISH_BREAKING_INLINE_PREFIX not in entry["command"]


@given("cursor hooks.json contains legacy fish-breaking inline commands")
def given_legacy_hooks(
    gherkin_context: GherkinContext,
    tmp_path: Path,
) -> None:
    repo_root = gherkin_context.payload["repo_root"]
    hooks_path = tmp_path / "hooks.json"
    hooks_path.write_text(json.dumps(load_legacy_fish_breaking_hooks(repo_root=repo_root)) + "\n")
    gherkin_context.payload["hooks_path"] = hooks_path
    gherkin_context.tmp_path = tmp_path


@when("cursor hooks are upserted for development mode")
def when_upsert_hooks(
    gherkin_context: GherkinContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hooks_dir = tmp_path / "cursor" / "hooks"
    monkeypatch.setattr("cyt.hook.cli_invocation.cursor_hooks_dir", lambda: hooks_dir)
    invocation = gherkin_context.payload["invocation"]
    entries = hook_setup.cursor_hook_entries(agent="cursor", invocation=invocation)
    hooks_path = gherkin_context.payload["hooks_path"]
    changed = hook_setup.upsert_cursor_hooks_into_file(
        hooks_path,
        **hook_setup.cursor_upsert_hook_kwargs(entries, config={}),
    )
    gherkin_context.payload["hooks_changed"] = changed
    gherkin_context.payload["hooks_data"] = json.loads(hooks_path.read_text(encoding="utf-8"))


@then("cursor hooks.json should reference shell wrapper scripts")
def then_hooks_reference_wrappers(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload["hooks_changed"] is True
    data = gherkin_context.payload["hooks_data"]
    before_submit = data["hooks"]["beforeSubmitPrompt"][0]["command"]
    assert before_submit.endswith(wrapper_suffix())
    assert Path(before_submit).is_file()


@then("cursor hooks.json should not contain fish-breaking inline env prefixes")
def then_hooks_avoid_inline_prefix(gherkin_context: GherkinContext) -> None:
    data = gherkin_context.payload["hooks_data"]
    for section in data["hooks"].values():
        if not isinstance(section, list):
            continue
        for entry in section:
            if isinstance(entry, dict) and "command" in entry:
                assert FISH_BREAKING_INLINE_PREFIX not in entry["command"]


@given("a Cursor workspace with stale rules injection")
def given_stale_rules(
    gherkin_context: GherkinContext,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "consumer-workspace"
    rules_path = workspace / RULES_REL_PATH
    rules_path.parent.mkdir(parents=True, exist_ok=True)
    rules_path.write_text(
        "---\nalwaysApply: true\n---\n\nstale injection from prior session\n",
        encoding="utf-8",
    )
    gherkin_context.payload["workspace"] = workspace


@when("the cyt-client shell wrapper runs from fish")
def when_wrapper_runs_from_fish(
    gherkin_context: GherkinContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if sys.platform == "win32":
        pytest.skip("fish wrapper scenario is Unix-only")
    if not fish_available():
        pytest.skip("fish not installed")

    wrapper_path = Path(
        cursor_dev_client_wrapper_command(tmp_path / "hooks", monkeypatch=monkeypatch),
    )
    workspace = gherkin_context.payload["workspace"]
    result = run_hook_wrapper_via_fish(
        wrapper_path,
        workspace=workspace,
        payload=SESSION_START_PAYLOAD,
    )
    gherkin_context.payload["wrapper_result"] = result


@then("cyt-client should continue successfully")
def then_client_continues(gherkin_context: GherkinContext) -> None:
    result = gherkin_context.payload["wrapper_result"]
    assert result.returncode == 0, result.stderr
    assert '"continue"' in result.stdout


@then("the Cursor rules file should reset to the lifecycle placeholder")
def then_rules_reset(gherkin_context: GherkinContext) -> None:
    workspace = gherkin_context.payload["workspace"]
    body = read_cursor_rules_injection(workspace)
    assert is_rules_placeholder_body(body)
