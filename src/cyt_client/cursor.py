"""Adapt Cursor hook stdin/stdout for cyt-client (stdlib only)."""

from __future__ import annotations

import json
from typing import Any

_CURSOR_EVENTS = frozenset({"beforeSubmitPrompt", "sessionStart", "sessionEnd"})
_CURSOR_RULES_LIFECYCLE_EVENTS = frozenset({"beforeSubmitPrompt", "sessionStart", "sessionEnd"})
_CURSOR_RULES_CLEANUP_EVENTS = frozenset({"sessionEnd"})


def cursor_hook_event_name(payload: dict[str, Any]) -> str | None:
    name = payload.get("hook_event_name") or payload.get("hookEventName")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def is_cursor_hook_payload(payload: dict[str, Any]) -> bool:
    event = cursor_hook_event_name(payload)
    return event in _CURSOR_EVENTS if event is not None else False


def is_cursor_rules_lifecycle_event(payload: dict[str, Any]) -> bool:
    event = cursor_hook_event_name(payload)
    return event in _CURSOR_RULES_LIFECYCLE_EVENTS if event is not None else False


def is_cursor_rules_cleanup_event(payload: dict[str, Any]) -> bool:
    event = cursor_hook_event_name(payload)
    return event in _CURSOR_RULES_CLEANUP_EVENTS if event is not None else False


def format_cursor_continue() -> str:
    return json.dumps({"continue": True})


def format_cursor_stdout(cyt_stdout: str) -> str:
    if not cyt_stdout.strip():
        return format_cursor_continue()

    try:
        data = json.loads(cyt_stdout)
    except json.JSONDecodeError:
        return format_cursor_continue()

    if not isinstance(data, dict):
        return format_cursor_continue()

    hook_output = data.get("hookSpecificOutput")
    if not isinstance(hook_output, dict):
        return format_cursor_continue()

    context = hook_output.get("additionalContext") or hook_output.get("additional_context")
    if isinstance(context, str) and context.strip():
        return json.dumps({"continue": True, "additional_context": context})

    return format_cursor_continue()
