"""Claude hook skills: transcript JSONL parsing."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

CLAUDE_SKILLS_DIR = Path("~/.claude/skills")


def _text_from_claude_content(content: object) -> str | None:
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


def _claude_message_text(message: dict[str, Any]) -> str | None:
    role = message.get("role")
    if role not in ("user", "assistant"):
        return None
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    return _text_from_claude_content(content)


def claude_turn_from_record(record: dict[str, Any]) -> str | None:
    message = record.get("message")
    if not isinstance(message, dict):
        return None
    return _claude_message_text(message)


def claude_assistant_from_record(record: dict[str, Any]) -> str | None:
    """Claude Code jsonl: top-level type assistant, or message.role assistant."""
    record_type = record.get("type")
    message = record.get("message")
    if not isinstance(message, dict):
        return None
    if message.get("role") != "assistant":
        return None
    if record_type not in (None, "assistant", "message"):
        return None
    return _text_from_claude_content(message.get("content"))


def model_from_claude_record(record: dict[str, Any]) -> str | None:
    message = record.get("message")
    if isinstance(message, dict):
        model = message.get("model")
        if isinstance(model, str) and model.strip():
            return model.strip()
    model = record.get("model")
    if isinstance(model, str) and model.strip():
        return model.strip()
    return None


def assistant_text_from_record(record: dict[str, Any]) -> tuple[str | None, str | None]:
    record_type = record.get("type")
    if record_type == "assistant" or (
        record_type in (None, "message") and isinstance(record.get("message"), dict)
    ):
        text = claude_assistant_from_record(record)
        if text:
            return text, None
    return None, None


def all_turns_text_from_records(records: list[Any]) -> str:
    parts: list[str] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        if text := claude_turn_from_record(record):
            parts.append(text)
    return "\n".join(parts)


def last_assistant_from_records(records: list[Any]) -> str | None:
    fallback_text: str | None = None
    for record in reversed(records):
        if not isinstance(record, dict):
            continue
        text, _phase = assistant_text_from_record(record)
        if not text:
            continue
        if fallback_text is None:
            fallback_text = text
    return fallback_text


def model_from_records(records: list[Any]) -> str | None:
    for record in reversed(records):
        if not isinstance(record, dict):
            continue
        if model := model_from_claude_record(record):
            return model
    return None


def normalize_payload(data: dict[str, Any]) -> dict[str, Any]:
    return dict(data)
