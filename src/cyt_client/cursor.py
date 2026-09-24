"""Adapt Cursor hook stdin/stdout for cyt-client (stdlib only)."""

from __future__ import annotations

import json
from typing import Any

from cyt_client.agent import looks_like_cursor_payload

_CURSOR_ROUTING_EVENTS = frozenset(
    {"sessionStart", "sessionEnd", "SessionStart", "SessionEnd"},
)
_CURSOR_RULES_LIFECYCLE_EVENTS = frozenset(
    {"beforeSubmitPrompt", "sessionStart", "sessionEnd", "SessionStart", "SessionEnd"},
)
_CURSOR_RULES_CLEANUP_EVENTS = frozenset({"sessionEnd", "SessionEnd"})
_SESSION_END_EVENTS = frozenset({"sessionEnd", "SessionEnd"})
_SESSION_START_EVENTS = frozenset({"sessionStart", "SessionStart"})


def hook_event_name(payload: dict[str, Any]) -> str | None:
    return cursor_hook_event_name(payload)


def _payload_has_prompt_or_tool(payload: dict[str, Any]) -> bool:
    for key in ("prompt", "tool_name", "toolName", "tool"):
        raw = payload.get(key)
        if isinstance(raw, str) and raw.strip():
            return True
    return False


def _session_id_from_payload(payload: dict[str, Any]) -> str | None:
    for key in ("session_id", "sessionId", "conversation_id", "conversationId"):
        raw = payload.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return None


def _infer_session_end_from_payload(payload: dict[str, Any]) -> bool:
    if hook_event_name(payload) is not None:
        return False
    session_id = _session_id_from_payload(payload)
    reason = payload.get("reason")
    return session_id is not None and isinstance(reason, str) and bool(reason.strip())


def _infer_session_start_from_payload(payload: dict[str, Any]) -> bool:
    if hook_event_name(payload) is not None:
        return False
    if _session_id_from_payload(payload) is None:
        return False
    if _infer_session_end_from_payload(payload) or _payload_has_prompt_or_tool(payload):
        return False
    return any(key in payload for key in ("composer_mode", "is_background_agent"))


def is_session_end_event(payload: dict[str, Any]) -> bool:
    event = hook_event_name(payload)
    if event in _SESSION_END_EVENTS:
        return True
    return _infer_session_end_from_payload(payload)


def is_session_start_event(payload: dict[str, Any]) -> bool:
    event = hook_event_name(payload)
    if event in _SESSION_START_EVENTS:
        return True
    return _infer_session_start_from_payload(payload)


def cursor_hook_event_name(payload: dict[str, Any]) -> str | None:
    name = payload.get("hook_event_name") or payload.get("hookEventName")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def is_cursor_hook_payload(payload: dict[str, Any]) -> bool:
    if looks_like_cursor_payload(payload):
        return True
    event = cursor_hook_event_name(payload)
    if event in _CURSOR_ROUTING_EVENTS:
        return True
    return _infer_session_start_from_payload(payload) or _infer_session_end_from_payload(payload)


def is_cursor_rules_lifecycle_event(payload: dict[str, Any]) -> bool:
    event = cursor_hook_event_name(payload)
    return event in _CURSOR_RULES_LIFECYCLE_EVENTS if event is not None else False


def is_cursor_rules_cleanup_event(payload: dict[str, Any]) -> bool:
    event = cursor_hook_event_name(payload)
    return event in _CURSOR_RULES_CLEANUP_EVENTS if event is not None else False


def format_cursor_post_tool_stdout() -> str:
    """Empty postToolUse response when no additional_context or MCP output rewrite."""
    return "{}"


def format_cursor_continue() -> str:
    return json.dumps({"continue": True})


def format_pre_tool_allow() -> str:
    return json.dumps({"permission": "allow"})


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
