"""Integration tests for dev/prod Cursor hook wrapper mode switching."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from cyt.hook import setup_wizard as hook_setup
from cyt.hook.cli_invocation import HookCliInvocation
from tests.support.hook_wrapper_mode_fixtures import (
    assert_wrapper_mode_on_disk,
    load_wrapper_mode_scenario,
    write_broken_hooks_json_dev_paths_prod_disk,
    write_cursor_hooks_for_mode,
    wrapper_script_names,
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


def _stub_hook_setup_prompts(monkeypatch: pytest.MonkeyPatch, *, action: str) -> None:
    monkeypatch.setattr(hook_setup, "_prompt_choice", lambda *args, **kwargs: action)
    monkeypatch.setattr(hook_setup, "_prompt_yes_no", lambda *args, **kwargs: True)
    monkeypatch.setattr(hook_setup, "_ensure_hook_credentials", lambda _config: None)


@pytest.mark.skipif(sys.platform == "win32", reason="Unix wrapper mode integration")
def test_hook_setup_update_repairs_broken_hooks_json_and_wrappers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scenario = load_wrapper_mode_scenario("broken_hooks_json_missing_wrapper_files")
    consumer = _prepare_cyt_checkout(tmp_path, monkeypatch)
    hooks_dir = tmp_path / "cursor" / "hooks"
    hooks_path = tmp_path / "cursor" / "hooks.json"
    config_path = tmp_path / "config.yaml"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hooks_path.parent.mkdir(parents=True, exist_ok=True)
    repo_root = Path.cwd()

    write_broken_hooks_json_dev_paths_prod_disk(
        hooks_path,
        hooks_dir,
        repo_root=repo_root,
    )
    monkeypatch.setattr(hook_setup, "CURSOR_HOOKS_PATH", hooks_path)
    monkeypatch.setattr("cyt.hook.cli_invocation.cursor_hooks_dir", lambda: hooks_dir)
    _stub_hook_setup_prompts(monkeypatch, action=str(scenario["action"]))
    monkeypatch.setattr(
        hook_setup,
        "detect_hook_cli_invocation",
        lambda: HookCliInvocation(mode="installed", repo_root=None),
    )

    with (
        patch("cyt.hook.setup_wizard.load_config", return_value={"skills": {"enabled": False}}),
        patch("cyt.config.save_user_config", return_value=True),
        patch("cyt.config.sync_config_in_place"),
        patch("cyt.hook.daemon.daemon_start"),
        patch("cyt.tools.cyt_mcp_setup.has_migratable_mcp_backends", return_value=False),
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

    assert_wrapper_mode_on_disk(hooks_dir, "prod")
    payload = json.loads(hooks_path.read_text(encoding="utf-8"))
    client_name, _ = wrapper_script_names("prod")
    assert payload["hooks"]["beforeSubmitPrompt"][0]["command"].endswith(client_name)
    _ = capsys.readouterr()


@pytest.mark.skipif(sys.platform == "win32", reason="Unix wrapper mode integration")
def test_hook_setup_skip_does_not_mutate_wrappers_when_switching_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = load_wrapper_mode_scenario("skip_does_not_mutate_wrappers_when_switching_mode")
    hooks_dir = tmp_path / "cursor" / "hooks"
    hooks_path = tmp_path / "cursor" / "hooks.json"
    hooks_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(hook_setup, "CURSOR_HOOKS_PATH", hooks_path)
    monkeypatch.setattr("cyt.hook.cli_invocation.cursor_hooks_dir", lambda: hooks_dir)
    repo_root = Path.cwd()
    dev_paths = write_cursor_hooks_for_mode(
        hooks_path,
        hooks_dir,
        "dev",
        repo_root=repo_root,
    )
    dev_text = dev_paths["client"].read_text(encoding="utf-8")
    _stub_hook_setup_prompts(monkeypatch, action=str(scenario["action"]))
    monkeypatch.setattr(
        hook_setup,
        "detect_hook_cli_invocation",
        lambda: HookCliInvocation(mode="installed", repo_root=None),
    )

    changed = hook_setup._install_cursor_hooks_for_target(
        "Cursor",
        hooks_path,
        debug=False,
        set_launch_agent=False,
        invocation=HookCliInvocation(mode="installed", repo_root=None),
        include_post_tool_use=False,
    )

    assert changed is False
    assert dev_paths["client"].read_text(encoding="utf-8") == dev_text
    assert_wrapper_mode_on_disk(hooks_dir, "dev")
