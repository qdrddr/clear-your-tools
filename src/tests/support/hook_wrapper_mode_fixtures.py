"""Shared fixtures for dev/prod Cursor hook wrapper mode switching."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Literal

from cyt.hook.cli_invocation import (
    HookCliInvocation,
    hook_shell_wrapper_paths,
    install_hook_shell_wrappers,
)

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "hook_wrapper_mode"
SCENARIOS_PATH = FIXTURES_DIR / "scenarios.json"

HookWrapperMode = Literal["dev", "prod"]


def load_wrapper_mode_scenarios() -> list[dict[str, Any]]:
    payload = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))
    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, list):
        raise ValueError(f"invalid scenarios payload in {SCENARIOS_PATH}")
    return [item for item in scenarios if isinstance(item, dict)]


def load_wrapper_mode_scenario(scenario_id: str) -> dict[str, Any]:
    for scenario in load_wrapper_mode_scenarios():
        if scenario.get("id") == scenario_id:
            return scenario
    raise KeyError(f"unknown hook wrapper scenario: {scenario_id}")


def wrapper_script_names(mode: HookWrapperMode) -> tuple[str, str]:
    if sys.platform == "win32":
        if mode == "dev":
            return ("cyt-client-dev.cmd", "cyt-hook-daemon-start-dev.cmd")
        return ("cyt-client.cmd", "cyt-hook-daemon-start.cmd")
    if mode == "dev":
        return ("cyt-client-dev.sh", "cyt-hook-daemon-start-dev.sh")
    return ("cyt-client.sh", "cyt-hook-daemon-start.sh")


def invocation_for_mode(mode: HookWrapperMode, *, repo_root: Path) -> HookCliInvocation:
    if mode == "dev":
        return HookCliInvocation(mode="dev", repo_root=repo_root)
    return HookCliInvocation(mode="installed", repo_root=None)


def install_wrappers_for_mode(
    hooks_dir: Path,
    mode: HookWrapperMode,
    *,
    repo_root: Path,
) -> dict[str, Path]:
    _ = hooks_dir
    return install_hook_shell_wrappers(
        invocation=invocation_for_mode(mode, repo_root=repo_root),
    )


def minimal_cursor_hooks_payload(*, client_command: str, daemon_command: str) -> dict[str, object]:
    client_entry = {"type": "command", "command": client_command, "timeout": 60}
    return {
        "version": 1,
        "hooks": {
            "beforeSubmitPrompt": [client_entry],
            "sessionStart": [
                {"type": "command", "command": daemon_command, "timeout": 60},
                client_entry,
            ],
            "sessionEnd": [client_entry],
            "preToolUse": [client_entry],
            "preCompact": [client_entry],
        },
    }


def write_cursor_hooks_for_mode(
    hooks_path: Path,
    hooks_dir: Path,
    mode: HookWrapperMode,
    *,
    repo_root: Path,
) -> dict[str, Path]:
    wrapper_paths = install_wrappers_for_mode(hooks_dir, mode, repo_root=repo_root)
    hooks_path.write_text(
        json.dumps(
            minimal_cursor_hooks_payload(
                client_command=str(wrapper_paths["client"]),
                daemon_command=str(wrapper_paths["daemon_start"]),
            ),
        )
        + "\n",
        encoding="utf-8",
    )
    return wrapper_paths


def write_broken_hooks_json_dev_paths_prod_disk(
    hooks_path: Path,
    hooks_dir: Path,
    *,
    repo_root: Path,
) -> dict[str, dict[str, Path]]:
    """Reproduce hooks.json -> dev paths while only production wrapper files exist."""
    dev_paths = hook_shell_wrapper_paths(
        invocation=invocation_for_mode("dev", repo_root=repo_root),
    )
    prod_paths = install_wrappers_for_mode(hooks_dir, "prod", repo_root=repo_root)
    hooks_path.write_text(
        json.dumps(
            minimal_cursor_hooks_payload(
                client_command=str(dev_paths["client"]),
                daemon_command=str(dev_paths["daemon_start"]),
            ),
        )
        + "\n",
        encoding="utf-8",
    )
    return {"dev": dev_paths, "prod": prod_paths}


def assert_wrapper_mode_on_disk(hooks_dir: Path, mode: HookWrapperMode) -> None:
    client_name, daemon_name = wrapper_script_names(mode)
    opposite_mode: HookWrapperMode = "prod" if mode == "dev" else "dev"
    opposite_client, opposite_daemon = wrapper_script_names(opposite_mode)
    assert (hooks_dir / client_name).is_file()
    assert (hooks_dir / daemon_name).is_file()
    assert not (hooks_dir / opposite_client).is_file()
    assert not (hooks_dir / opposite_daemon).is_file()
