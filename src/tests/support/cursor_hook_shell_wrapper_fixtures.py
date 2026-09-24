"""Shared fixtures for Cursor hook shell wrapper (fish/bash) tests."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from cyt.hook.cli_invocation import (
    HookCliInvocation,
    build_uv_run_dev_command,
    cursor_hook_client_command,
    cyt_client_cli_script_relpath,
    install_hook_shell_wrappers,
    repo_root_from_proxy_cli_script,
)

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "cursor_hooks"
LEGACY_FISH_BREAKING_HOOKS = FIXTURES_DIR / "legacy_fish_breaking_hooks.json"
FISH_BREAKING_INLINE_PREFIX = "CYT_WORKSPACE=${workspaceFolder}"
SESSION_START_PAYLOAD: dict[str, Any] = {
    "session_id": "wrapper-test-session",
    "composer_mode": "agent",
    "is_background_agent": False,
}


def fish_available() -> bool:
    return shutil.which("fish") is not None


def bash_available() -> bool:
    return shutil.which("bash") is not None


def dev_repo_root() -> Path:
    repo_root = repo_root_from_proxy_cli_script()
    assert repo_root is not None
    return repo_root


def legacy_fish_breaking_client_command(*, repo_root: Path | None = None) -> str:
    root = repo_root or dev_repo_root()
    return (
        f"{FISH_BREAKING_INLINE_PREFIX} "
        f"{build_uv_run_dev_command(root, cyt_client_cli_script_relpath())}"
    )


def load_legacy_fish_breaking_hooks(*, repo_root: Path | None = None) -> dict[str, Any]:
    """Load the legacy hooks.json fixture with repo-specific uv commands."""
    root = repo_root or dev_repo_root()
    text = LEGACY_FISH_BREAKING_HOOKS.read_text(encoding="utf-8").replace(
        "__REPO_ROOT__",
        str(root),
    )
    loaded: Any = json.loads(text)
    assert isinstance(loaded, dict)
    return loaded


def install_dev_hook_wrappers(
    hooks_dir: Path,
    *,
    repo_root: Path | None = None,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> dict[str, Path]:
    from cyt.hook import cli_invocation as hook_cli

    root = repo_root or dev_repo_root()
    if monkeypatch is not None:
        monkeypatch.setattr(hook_cli, "cursor_hooks_dir", lambda: hooks_dir)
    invocation = HookCliInvocation(mode="dev", repo_root=root)
    return install_hook_shell_wrappers(invocation=invocation)


def cursor_dev_client_wrapper_command(
    hooks_dir: Path,
    *,
    repo_root: Path | None = None,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> str:
    from cyt.hook import cli_invocation as hook_cli

    root = repo_root or dev_repo_root()
    if monkeypatch is not None:
        monkeypatch.setattr(hook_cli, "cursor_hooks_dir", lambda: hooks_dir)
    invocation = HookCliInvocation(mode="dev", repo_root=root)
    return cursor_hook_client_command(invocation=invocation)


def wrapper_suffix() -> str:
    return "cyt-client-dev.cmd" if sys.platform == "win32" else "cyt-client-dev.sh"


def run_hook_wrapper_direct(
    wrapper_path: Path,
    *,
    workspace: Path,
    payload: dict[str, Any] | None = None,
    timeout: float = 120.0,
) -> subprocess.CompletedProcess[str]:
    """Execute a wrapper the way Cursor does: run the script path directly."""
    body = json.dumps(payload or SESSION_START_PAYLOAD)
    env = {
        **os.environ,
        "CURSOR_PROJECT_DIR": str(workspace),
        "CYT_HOOK_QUIET": "1",
    }
    return subprocess.run(
        [str(wrapper_path)],
        input=body,
        text=True,
        capture_output=True,
        env=env,
        timeout=timeout,
        check=False,
    )


def run_hook_wrapper_via_fish(
    wrapper_path: Path,
    *,
    workspace: Path,
    payload: dict[str, Any] | None = None,
    timeout: float = 120.0,
) -> subprocess.CompletedProcess[str]:
    """Simulate Cursor invoking the hook while fish is the login shell."""
    body = json.dumps(payload or SESSION_START_PAYLOAD)
    quoted_wrapper = shlex.quote(str(wrapper_path))
    quoted_workspace = shlex.quote(str(workspace))
    fish_script = (
        f"set -x CURSOR_PROJECT_DIR {quoted_workspace}; "
        f"set -x CYT_HOOK_QUIET 1; "
        f"printf '%s' {shlex.quote(body)} | {quoted_wrapper}"
    )
    return subprocess.run(
        ["fish", "-c", fish_script],
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def run_fish_hook_command(command: str) -> subprocess.CompletedProcess[str]:
    """Run a hook command string the way Cursor does when fish is the login shell."""
    return subprocess.run(
        ["fish", "-c", command],
        text=True,
        capture_output=True,
        check=False,
    )


def assert_fish_rejects_inline_workspace_prefix(command: str) -> None:
    """Fish rejects bash-style ``CYT_WORKSPACE=${workspaceFolder}`` inline prefixes."""
    if not fish_available():
        return
    result = run_fish_hook_command(command)
    assert result.returncode != 0, "expected fish to reject inline workspaceFolder prefix"
    assert "Variables cannot be bracketed" in result.stderr


def assert_wrapper_command_is_fish_safe(command: str) -> None:
    """Inline env prefixes must fail under fish; wrapper paths must not use them."""
    if FISH_BREAKING_INLINE_PREFIX in command:
        assert_fish_rejects_inline_workspace_prefix(command)
        return
    assert "CYT_WORKSPACE=" not in command
    assert "${workspaceFolder}" not in command
    if command.endswith(".sh"):
        path = Path(command)
        assert path.is_file(), command
        assert path.stat().st_mode & 0o111


assert_fish_rejects_inline_hook_command = assert_fish_rejects_inline_workspace_prefix
