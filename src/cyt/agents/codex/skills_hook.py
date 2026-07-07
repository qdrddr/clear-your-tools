"""Codex hook skills: transcript JSONL parsing."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

CODEX_SKILLS_DIR = Path("~/.codex/skills")


def _text_from_codex_content(content: object) -> str | None:
    if isinstance(content, str) and content.strip():
        return content.strip()
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        mapping = cast(dict[str, Any], block)
        if mapping.get("type") not in ("output_text", "input_text", "text"):
            continue
        text = mapping.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    return "\n".join(parts) if parts else None


def model_from_codex_record(record: dict[str, Any]) -> str | None:
    if record.get("type") != "turn_context":
        return None
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None
    model = payload.get("model")
    if isinstance(model, str) and model.strip():
        return model.strip()
    collaboration = payload.get("collaboration_mode")
    if isinstance(collaboration, dict):
        settings = collaboration.get("settings")
        if isinstance(settings, dict):
            nested = settings.get("model")
            if isinstance(nested, str) and nested.strip():
                return nested.strip()
    return None


def codex_message_text_from_payload(payload: dict[str, Any]) -> str | None:
    if payload.get("type") != "message":
        return None
    role = payload.get("role")
    if role not in ("user", "assistant"):
        return None
    return _text_from_codex_content(payload.get("content"))


def codex_turn_from_record(record: dict[str, Any]) -> str | None:
    if record.get("type") != "response_item":
        return None
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None
    return codex_message_text_from_payload(payload)


def assistant_text_from_record(record: dict[str, Any]) -> tuple[str | None, str | None]:
    record_type = record.get("type")
    if record_type == "response_item":
        payload = record.get("payload")
        if not isinstance(payload, dict):
            return None, None
        if payload.get("type") != "message" or payload.get("role") != "assistant":
            return None, None
        text = _text_from_codex_content(payload.get("content"))
        phase = payload.get("phase")
        phase_str = phase if isinstance(phase, str) else None
        return text, phase_str
    return None, None


def all_turns_text_from_records(records: list[Any]) -> str:
    parts: list[str] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        if text := codex_turn_from_record(record):
            parts.append(text)
    return "\n".join(parts)


def last_assistant_from_records(records: list[Any]) -> str | None:
    fallback_text: str | None = None
    for record in reversed(records):
        if not isinstance(record, dict):
            continue
        text, phase = assistant_text_from_record(record)
        if not text:
            continue
        if phase == "final_answer":
            return text
        if fallback_text is None:
            fallback_text = text
    return fallback_text


def model_from_records(records: list[Any]) -> str | None:
    for record in reversed(records):
        if not isinstance(record, dict):
            continue
        if model := model_from_codex_record(record):
            return model
    return None


def normalize_payload(data: dict[str, Any]) -> dict[str, Any]:
    return dict(data)
