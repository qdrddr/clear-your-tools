"""Cursor hook config install."""

from __future__ import annotations

from cyt.hook.setup_wizard import (
    CURSOR_HOOKS_PATH,
    CURSOR_SKILLS_DIR,
    cursor_hook_entries,
    upsert_cursor_hooks_into_file,
)

__all__ = [
    "CURSOR_HOOKS_PATH",
    "CURSOR_SKILLS_DIR",
    "cursor_hook_entries",
    "upsert_cursor_hooks_into_file",
]
