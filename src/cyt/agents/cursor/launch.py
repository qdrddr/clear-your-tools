"""Cursor IDE launcher with CYT hook injection."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from cyt.agents.cursor.hook import (
    CURSOR_HOOKS_PATH,
    cursor_hook_entries,
    upsert_cursor_hooks_into_file,
)
from cyt.config import inject_via, load_config, save_user_config
from cyt.hook.cli_invocation import detect_hook_cli_invocation
from cyt.proxy.setup_wizard import _prompt_yes_no

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


def ensure_cursor_inject_via_hook(
    config_path: Path,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Ensure ``pruning.inject_via`` is ``hook``; prompt interactively when set to ``proxy``."""
    if inject_via(config) == "hook":
        return config

    if not sys.stdin.isatty():
        raise SystemExit(
            "Cursor only supports hook injection (pruning.inject_via: hook). "
            "Update your CYT config and retry.",
        )

    if not _prompt_yes_no(
        "Cursor only supports hook injection. Switch pruning.inject_via to hook?",
        default_yes=True,
    ):
        raise SystemExit("Cursor launch requires pruning.inject_via: hook.")

    save_user_config(
        config_path,
        {"pruning": {"inject_via": "hook"}},
        apply_bundled_sections=False,
    )
    return load_config(config_path)


def ensure_cursor_hooks_for_launch(*, quiet: bool = False) -> bool:
    """Install or refresh Cursor hooks in ``~/.cursor/hooks.json``."""
    path = CURSOR_HOOKS_PATH.expanduser()
    invocation = detect_hook_cli_invocation()
    entries = cursor_hook_entries(agent="cursor", invocation=invocation)
    changed = upsert_cursor_hooks_into_file(
        path,
        before_submit_entry=entries["before_submit"],
        session_start_entries=entries["session_start"],
        session_end_entry=entries["session_end"],
    )
    if changed and not quiet:
        print(f"Updated CYT hooks in {path}")
    return changed


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
