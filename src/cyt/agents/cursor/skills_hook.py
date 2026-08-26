"""Cursor hook skills: payload normalize + transcript JSONL parsing."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

CURSOR_SKILLS_DIR = Path("~/.cursor/skills")

_CURSOR_EVENTS = frozenset({"beforeSubmitPrompt", "sessionStart", "sessionEnd"})


def _normalize_cursor_hook_events(merged: dict[str, Any]) -> None:
    event = merged.get("hook_event_name") or merged.get("hookEventName")
    if not isinstance(event, str):
        return
    if event == "beforeSubmitPrompt":
        merged["hook_event_name"] = "UserPromptSubmit"
    elif event == "sessionStart":
        merged["hook_event_name"] = "SessionStart"


def _normalize_cursor_workspace_path(raw: str) -> str:
    text = raw.strip()
    if len(text) >= 4 and text[0] == "/" and text[2] == ":":
        drive = text[1].upper()
        rest = text[3:].lstrip("/\\")
        return str(Path(f"{drive}:/{rest}"))
    return text


def _apply_cursor_workspace_fields(merged: dict[str, Any]) -> None:
    if not merged.get("cwd"):
        roots = merged.get("workspace_roots")
        if isinstance(roots, list) and roots:
            first = roots[0]
            if isinstance(first, str) and first.strip():
                merged["cwd"] = _normalize_cursor_workspace_path(first)
    elif isinstance(merged.get("cwd"), str):
        merged["cwd"] = _normalize_cursor_workspace_path(str(merged["cwd"]))

    if isinstance(merged.get("workspace_roots"), list):
        merged["workspace_roots"] = [
            _normalize_cursor_workspace_path(str(root)) if isinstance(root, str) else root
            for root in merged["workspace_roots"]
        ]

    if not merged.get("session_id"):
        conversation_id = merged.get("conversation_id")
        if isinstance(conversation_id, str) and conversation_id.strip():
            merged["session_id"] = conversation_id.strip()


def normalize_cursor_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Map Cursor hook fields to the cyt hook shape (server-side only)."""
    merged = dict(data)
    _normalize_cursor_hook_events(merged)
    _apply_cursor_workspace_fields(merged)
    return merged


def _text_from_cursor_content(content: object) -> str | None:
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        mapping = cast(dict[str, Any], block)
        if mapping.get("type") != "text":
            continue
        text = mapping.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    return "\n".join(parts) if parts else None


def cursor_turn_from_record(record: dict[str, Any]) -> str | None:
    """Return user or assistant text from a Cursor agent-transcript record."""
    role = record.get("role")
    if role not in ("user", "assistant"):
        return None
    message = record.get("message")
    if not isinstance(message, dict):
        return None
    return _text_from_cursor_content(message.get("content"))


def cursor_assistant_from_record(record: dict[str, Any]) -> str | None:
    """Cursor agent-transcripts: top-level role assistant + message.content[].text."""
    if record.get("role") != "assistant":
        return None
    message = record.get("message")
    if not isinstance(message, dict):
        return None
    return _text_from_cursor_content(message.get("content"))


def all_turns_text_from_records(records: list[Any]) -> str:
    parts: list[str] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        if text := cursor_turn_from_record(record):
            parts.append(text)
    return "\n".join(parts)


def last_assistant_from_records(records: list[Any]) -> str | None:
    for record in reversed(records):
        if not isinstance(record, dict):
            continue
        if text := cursor_assistant_from_record(record):
            return text
    return None


def looks_like_cursor_hook(data: dict[str, Any]) -> bool:
    event = data.get("hook_event_name") or data.get("hookEventName")
    if isinstance(event, str) and event in _CURSOR_EVENTS:
        return True
    if data.get("workspace_roots") is not None:
        return True
    return False


def normalize_payload(data: dict[str, Any]) -> dict[str, Any]:
    return normalize_cursor_payload(data)
