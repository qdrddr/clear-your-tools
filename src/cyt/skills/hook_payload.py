"""Normalize agent hook stdin JSON (Codex + Claude Code)."""

from __future__ import annotations

from typing import Any


def normalize_hook_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Merge nested ``payload`` fields into a flat hook view.

    Claude Code sends flat JSON on stdin (``session_id``, ``model``, ``prompt`` at the top level).
    Some hook integrations nest those fields under ``payload``; top-level keys win on conflicts.
    """
    nested = data.get("payload")
    if not isinstance(nested, dict):
        return dict(data)
    merged = dict(nested)
    for key, value in data.items():
        if key != "payload":
            merged[key] = value
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
