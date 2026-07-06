"""Adapt Cursor hook stdin/stdout for cyt-client (stdlib only)."""

from __future__ import annotations

import json
from typing import Any

_CURSOR_EVENTS = frozenset({"beforeSubmitPrompt", "sessionStart"})
_CURSOR_TO_CYT_EVENT = {
    "beforeSubmitPrompt": "UserPromptSubmit",
    "sessionStart": "SessionStart",
}


def cursor_hook_event_name(payload: dict[str, Any]) -> str | None:
    name = payload.get("hook_event_name") or payload.get("hookEventName")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def is_cursor_hook_payload(payload: dict[str, Any]) -> bool:
    event = cursor_hook_event_name(payload)
    return event in _CURSOR_EVENTS if event is not None else False


def adapt_cursor_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Map Cursor hook fields to the cyt hook shape cyt-client already understands."""
    event = cursor_hook_event_name(data)
    if event not in _CURSOR_EVENTS:
        return data

    adapted = dict(data)
    adapted["hook_event_name"] = _CURSOR_TO_CYT_EVENT[event]

    if not adapted.get("cwd"):
        roots = adapted.get("workspace_roots")
        if isinstance(roots, list) and roots:
            first = roots[0]
            if isinstance(first, str) and first.strip():
                adapted["cwd"] = first.strip()

    if not adapted.get("session_id"):
        conversation_id = adapted.get("conversation_id")
        if isinstance(conversation_id, str) and conversation_id.strip():
            adapted["session_id"] = conversation_id.strip()

    return adapted


def format_cursor_stdout(cyt_stdout: str) -> str:
    if not cyt_stdout.strip():
        return json.dumps({"continue": True})

    try:
        data = json.loads(cyt_stdout)
    except json.JSONDecodeError:
        return json.dumps({"continue": True})

    if not isinstance(data, dict):
        return json.dumps({"continue": True})

    hook_output = data.get("hookSpecificOutput")
    if not isinstance(hook_output, dict):
        return json.dumps({"continue": True})

    context = hook_output.get("additionalContext") or hook_output.get("additional_context")
    if isinstance(context, str) and context.strip():
        return json.dumps({"continue": True, "additional_context": context})

    return json.dumps({"continue": True})
