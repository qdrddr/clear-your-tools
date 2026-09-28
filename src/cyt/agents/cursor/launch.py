"""Cursor IDE launcher with CYT hook injection."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from cyt.agents.cursor.hook import (
    CURSOR_HOOKS_PATH,
    cursor_hook_entries,
    cursor_upsert_hook_kwargs,
    upsert_cursor_hooks_into_file,
)
from cyt.config import load_config
from cyt.hook.cli_invocation import (
    detect_hook_cli_invocation,
    ensure_hook_wrapper_scripts_for_hooks_file,
)
from cyt.hook.setup_wizard import (
    _collect_cyt_hook_commands,
    _cursor_hooks_need_wrapper_refresh,
    _load_json_object,
    cursor_desired_hook_commands,
)

_CURSOR_CANDIDATES = (
    Path("/Applications/Cursor.app/Contents/Resources/app/bin/cursor"),
    Path.home()
    / "Applications"
    / "Cursor.app"
    / "Contents"
    / "Resources"
    / "app"
    / "bin"
    / "cursor",
)


def find_cursor() -> str:
    """Locate the Cursor CLI binary."""
    if found := shutil.which("cursor"):
        return found
    for candidate in _CURSOR_CANDIDATES:
        if candidate.is_file():
            return str(candidate)
    raise SystemExit(
        "Cursor CLI not found. Install it from Cursor (Shell Command: Install 'cursor' command) "
        "or add `cursor` to PATH.",
    )


def ensure_cursor_hooks_for_launch(*, quiet: bool = False) -> bool:
    """Install or refresh Cursor hooks in ``~/.cursor/hooks.json``."""
    from cyt.hook.setup_wizard import _apply_cursor_hook_wrappers

    path = CURSOR_HOOKS_PATH.expanduser()
    invocation = detect_hook_cli_invocation()
    config = load_config()
    ensure_hook_wrapper_scripts_for_hooks_file(path)
    entries = cursor_hook_entries(
        agent="cursor",
        invocation=invocation,
        install_wrappers=False,
    )
    hooks_section = _load_json_object(path).get("hooks")
    existing_commands = _collect_cyt_hook_commands(hooks_section)
    desired_commands = cursor_desired_hook_commands(
        agent="cursor",
        invocation=invocation,
        config=config,
        install_wrappers=False,
    )
    needs_update = _cursor_hooks_need_wrapper_refresh(existing_commands, desired_commands)
    if needs_update:
        _apply_cursor_hook_wrappers(invocation=invocation, set_launch_agent=False)
    changed = upsert_cursor_hooks_into_file(
        path,
        **cursor_upsert_hook_kwargs(entries, config=config),
    )
    if (changed or needs_update) and not quiet:
        print(f"Updated CYT hooks in {path}")
    return changed or needs_update


def run(
    *,
    agent_args: list[str],
    config: dict[str, Any] | None = None,
    port: int | None = None,
    endpoint: str | None = None,
    auth_binding: object | None = None,
    use_proxy: bool = True,
    switch_provider: bool = False,
) -> int:
    del config, port, endpoint, auth_binding, use_proxy, switch_provider
    """Launch Cursor with optional CLI args (e.g. a workspace path)."""
    cursor = find_cursor()
    try:
        result = subprocess.run([cursor, *agent_args], check=False)
    except OSError as exc:
        raise SystemExit(f"Failed to launch Cursor: {exc}") from exc
    return int(result.returncode)
