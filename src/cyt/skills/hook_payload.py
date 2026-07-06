"""Normalize agent hook stdin JSON (Codex + Claude Code)."""

from __future__ import annotations

from typing import Any


def _merge_hook_payload_data(data: dict[str, Any]) -> dict[str, Any]:
    nested = data.get("payload")
    if isinstance(nested, dict):
        merged = dict(nested)
        for key, value in data.items():
            if key != "payload":
                merged[key] = value
        return merged
    return dict(data)


def _normalize_cursor_hook_events(merged: dict[str, Any]) -> None:
    event = merged.get("hook_event_name") or merged.get("hookEventName")
    if not isinstance(event, str):
        return
    if event == "beforeSubmitPrompt":
        merged["hook_event_name"] = "UserPromptSubmit"
    elif event == "sessionStart":
        merged["hook_event_name"] = "SessionStart"


def _apply_cursor_workspace_fields(merged: dict[str, Any]) -> None:
    if not merged.get("cwd"):
        roots = merged.get("workspace_roots")
        if isinstance(roots, list) and roots:
            first = roots[0]
            if isinstance(first, str) and first.strip():
                merged["cwd"] = first.strip()

    if not merged.get("session_id"):
        conversation_id = merged.get("conversation_id")
        if isinstance(conversation_id, str) and conversation_id.strip():
            merged["session_id"] = conversation_id.strip()


def normalize_hook_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Merge nested ``payload`` fields into a flat hook view.

    Claude Code sends flat JSON on stdin (``session_id``, ``model``, ``prompt`` at the top level).
    Some hook integrations nest those fields under ``payload``; top-level keys win on conflicts.
    Cursor sends ``beforeSubmitPrompt`` / ``sessionStart`` with ``workspace_roots`` and
    ``conversation_id``; map those to the cyt hook shape.
    """
    merged = _merge_hook_payload_data(data)
    _normalize_cursor_hook_events(merged)
    _apply_cursor_workspace_fields(merged)
    return merged


def hook_event_name(payload: dict[str, Any]) -> str | None:
    name = payload.get("hook_event_name") or payload.get("hookEventName")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def hook_cwd(payload: dict[str, Any]) -> str | None:
    cwd = payload.get("cwd")
    return cwd if isinstance(cwd, str) and cwd.strip() else None


def session_id(payload: dict[str, Any]) -> str | None:
    session_id = payload.get("session_id") or payload.get("sessionId")
    if isinstance(session_id, str) and session_id.strip():
        return session_id.strip()
    return None


def model_from_payload(payload: dict[str, Any]) -> str | None:
    model = payload.get("model")
    if isinstance(model, str) and model.strip():
        return model.strip()
    return None


def prompt_from_payload(payload: dict[str, Any]) -> str | None:
    prompt = payload.get("prompt")
    if isinstance(prompt, str) and prompt.strip():
        return prompt.strip()
    return None
