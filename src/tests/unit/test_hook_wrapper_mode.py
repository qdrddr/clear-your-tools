"""Unit tests for dev/prod Cursor hook wrapper mode switching."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from cyt.hook import setup_wizard as hook_setup
from cyt.hook.cli_invocation import (
    HookCliInvocation,
    cursor_hook_client_command,
    hook_shell_wrapper_paths,
)
from tests.support.cursor_hook_shell_wrapper_fixtures import dev_repo_root
from tests.support.hook_wrapper_mode_fixtures import (
    load_wrapper_mode_scenario,
    wrapper_script_names,
    write_broken_hooks_json_dev_paths_prod_disk,
    write_cursor_hooks_for_mode,
)


@pytest.mark.parametrize(
    ("scenario_id",),
    [
        ("skip_does_not_mutate_wrappers_when_switching_mode",),
        ("update_switches_dev_to_prod",),
        ("update_switches_prod_to_dev",),
        ("broken_hooks_json_missing_wrapper_files",),
    ],
)
@pytest.mark.skipif(sys.platform == "win32", reason="Unix wrapper mode semantics")
def test_install_cursor_hooks_respects_wrapper_mode_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario_id: str,
) -> None:
    scenario = load_wrapper_mode_scenario(scenario_id)
    hooks_dir = tmp_path / "hooks"
    hooks_path = tmp_path / "hooks.json"
    monkeypatch.setattr("cyt.hook.cli_invocation.cursor_hooks_dir", lambda: hooks_dir)
    repo_root = dev_repo_root()
    from_mode = scenario["from_mode"]
    to_mode = scenario["to_mode"]
    disk_mode = scenario.get("disk_mode", from_mode)

    if scenario_id == "broken_hooks_json_missing_wrapper_files":
        write_broken_hooks_json_dev_paths_prod_disk(
            hooks_path,
            hooks_dir,
            repo_root=repo_root,
        )
    else:
        write_cursor_hooks_for_mode(
            hooks_path,
            hooks_dir,
            from_mode,
            repo_root=repo_root,
        )
        if disk_mode != from_mode:
            write_cursor_hooks_for_mode(
                hooks_path,
                hooks_dir,
                disk_mode,
                repo_root=repo_root,
            )

    monkeypatch.setattr(
        hook_setup,
        "_prompt_choice",
        lambda *args, **kwargs: scenario["action"],
    )

    to_invocation = (
        HookCliInvocation(mode="installed", repo_root=None)
        if to_mode == "prod"
        else HookCliInvocation(mode="dev", repo_root=repo_root)
    )
    changed = hook_setup._install_cursor_hooks_for_target(
        "Cursor",
        hooks_path,
        debug=False,
        set_launch_agent=False,
        invocation=to_invocation,
        include_post_tool_use=False,
    )

    assert changed is scenario["expect_hooks_changed"]
    expected_mode = (
        from_mode
        if not scenario["expect_hooks_changed"]
        else scenario["expect_wrapper_mode_on_disk"]
    )
    client_name, daemon_name = wrapper_script_names(expected_mode)
    assert (hooks_dir / client_name).is_file()
    assert (hooks_dir / daemon_name).is_file()

    payload = json.loads(hooks_path.read_text(encoding="utf-8"))
    before_submit = payload["hooks"]["beforeSubmitPrompt"][0]["command"]
    if scenario["expect_hooks_changed"]:
        assert before_submit.endswith(client_name)
    elif from_mode == "dev":
        assert before_submit.endswith("cyt-client-dev.sh")


def test_cursor_hooks_need_wrapper_refresh_detects_missing_wrapper_files(
    tmp_path: Path,
) -> None:
    missing = str(tmp_path / "missing" / "cyt-client-dev.sh")
    existing = [missing]
    desired = [str(tmp_path / "cyt-client.sh")]
    assert hook_setup._cursor_hooks_need_wrapper_refresh(existing, desired) is True


def test_cursor_hooks_need_wrapper_refresh_false_when_paths_match_and_exist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hooks_dir = tmp_path / "hooks"
    monkeypatch.setattr("cyt.hook.cli_invocation.cursor_hooks_dir", lambda: hooks_dir)
    repo_root = dev_repo_root()
    paths = hook_shell_wrapper_paths(
        invocation=HookCliInvocation(mode="dev", repo_root=repo_root),
    )
    hooks_dir.mkdir(parents=True)
    paths["client"].write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    command = str(paths["client"])
    assert hook_setup._cursor_hooks_need_wrapper_refresh([command], [command]) is False


@pytest.mark.skipif(sys.platform == "win32", reason="Unix wrapper mode semantics")
def test_cursor_hook_client_command_without_install_does_not_write_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hooks_dir = tmp_path / "hooks"
    monkeypatch.setattr("cyt.hook.cli_invocation.cursor_hooks_dir", lambda: hooks_dir)
    repo_root = dev_repo_root()
    command = cursor_hook_client_command(
        invocation=HookCliInvocation(mode="dev", repo_root=repo_root),
        install_wrappers=False,
    )
    assert command.endswith("cyt-client-dev.sh")
    assert not hooks_dir.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="Unix wrapper mode semantics")
def test_cursor_hook_entries_planning_phase_does_not_write_wrappers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hooks_dir = tmp_path / "hooks"
    monkeypatch.setattr("cyt.hook.cli_invocation.cursor_hooks_dir", lambda: hooks_dir)
    repo_root = dev_repo_root()
    entries = hook_setup.cursor_hook_entries(
        agent="cursor",
        invocation=HookCliInvocation(mode="dev", repo_root=repo_root),
        install_wrappers=False,
        include_post_tool_use=False,
    )
    assert entries["before_submit"]["command"].endswith("cyt-client-dev.sh")
    assert not hooks_dir.exists()
