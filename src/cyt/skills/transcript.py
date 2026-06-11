"""Extract assistant context from Claude Code / Codex session transcript jsonl."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, cast

from cyt.proxy.anthropic import format_search_query
from cyt.skills.hook_payload import prompt_from_payload

logger = logging.getLogger(__name__)


def transcript_path_from_payload(payload: dict[str, Any]) -> str | None:
    raw = payload.get("transcript_path")
    if raw is None:
        return None
    if not isinstance(raw, str):
        return None
    path = raw.strip()
    return path or None


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


def _text_from_codex_content(content: object) -> str | None:
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        mapping = cast(dict[str, Any], block)
        if mapping.get("type") != "output_text":
            continue
        text = mapping.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    return "\n".join(parts) if parts else None


def _claude_assistant_from_record(record: dict[str, Any]) -> str | None:
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


def _assistant_text_from_record(record: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return (text, phase) for assistant rows; phase only set for Codex."""
    record_type = record.get("type")

    if record_type == "assistant" or (
        record_type in (None, "message") and isinstance(record.get("message"), dict)
    ):
        text = _claude_assistant_from_record(record)
        if text:
            return text, None

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


def last_assistant_from_transcript(path: str) -> str | None:
    """Scan transcript jsonl backwards; return last assistant text when found."""
    transcript = Path(path).expanduser()
    if not transcript.is_file():
        return None

    fallback_text: str | None = None
    try:
        lines = transcript.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        logger.debug("skills transcript read failed: %s", exc)
        return None

    for line in reversed(lines):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue

        text, phase = _assistant_text_from_record(record)
        if not text:
            continue
        if phase == "final_answer":
            return text
        if fallback_text is None:
            fallback_text = text

    return fallback_text


def skills_search_query(
    user_prompt: str,
    *,
    transcript_path: str | None = None,
    assistant_message: str | None = None,
) -> str | None:
    """Build format_search_query(user, assistant) for skills pruners (BM25/rerank/LLM)."""
    prompt = user_prompt.strip()
    if not prompt:
        return None

    assistant = assistant_message
    if assistant is None and transcript_path:
        assistant = last_assistant_from_transcript(transcript_path)

    return format_search_query(prompt, assistant)


def skills_search_query_from_hook_payload(payload: dict[str, Any]) -> str | None:
    """Build format_search_query(user, assistant) from hook stdin fields."""
    prompt = prompt_from_payload(payload)
    if not prompt:
        return None

    transcript_path = transcript_path_from_payload(payload)
    return skills_search_query(prompt, transcript_path=transcript_path)
