"""Extract assistant context from Claude Code / Codex session transcript jsonl."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Literal, cast

from cyt.proxy.anthropic import format_search_query
from cyt.skills.agents import CYT_LAUNCH_AGENT_ENV
from cyt.skills.hook_payload import model_from_payload, prompt_from_payload

logger = logging.getLogger(__name__)

TranscriptAgent = Literal["claude", "codex"]
TranscriptSource = Literal["inline", "file", "none"]


def transcript_path_from_payload(payload: dict[str, Any]) -> str | None:
    raw = payload.get("transcript_path")
    if raw is None:
        return None
    if not isinstance(raw, str):
        return None
    path = raw.strip()
    return path or None


def infer_transcript_agent(path: str | None) -> TranscriptAgent | None:
    """Infer Claude vs Codex from transcript_path heuristics or CYT_LAUNCH_AGENT."""
    if path:
        normalized = path.replace("\\", "/").lower()
        if "/.codex/" in normalized or normalized.startswith("~/.codex/"):
            return "codex"
        if "/.claude/" in normalized or normalized.startswith("~/.claude/"):
            return "claude"

    env_value = os.environ.get(CYT_LAUNCH_AGENT_ENV)
    if isinstance(env_value, str):
        agent = env_value.strip().lower()
        if agent in ("claude", "codex"):
            return cast(TranscriptAgent, agent)
    return None


def _load_transcript_file(path: Path) -> list[Any]:
    text = path.read_text(encoding="utf-8")
    try:
        parsed = json.loads(text)
        return [parsed]
    except json.JSONDecodeError:
        pass

    items: list[Any] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("PWD:"):
            continue
        try:
            items.append(json.loads(stripped))
        except json.JSONDecodeError:
            continue
    if items:
        return items

    return [text]


def transcript_records_from_payload(
    payload: dict[str, Any],
    *,
    allow_file_read: bool,
) -> list[Any] | None:
    """Return transcript records from cyt_transcript or, on stdin path only, transcript_path file."""
    inline = payload.get("cyt_transcript")
    if isinstance(inline, list) and inline:
        return inline

    if not allow_file_read:
        return None

    transcript_path = transcript_path_from_payload(payload)
    if transcript_path is None:
        return None

    path = Path(transcript_path).expanduser()
    if not path.is_file():
        return None

    try:
        return _load_transcript_file(path)
    except OSError as exc:
        logger.debug("skills transcript read failed: %s", exc)
        return None


def transcript_source_from_payload(
    payload: dict[str, Any],
    *,
    allow_file_read: bool,
) -> TranscriptSource:
    inline = payload.get("cyt_transcript")
    if isinstance(inline, list) and inline:
        return "inline"
    if allow_file_read and transcript_path_from_payload(payload):
        records = transcript_records_from_payload(payload, allow_file_read=True)
        if records:
            return "file"
    return "none"


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


def _model_from_claude_record(record: dict[str, Any]) -> str | None:
    message = record.get("message")
    if isinstance(message, dict):
        model = message.get("model")
        if isinstance(model, str) and model.strip():
            return model.strip()
    model = record.get("model")
    if isinstance(model, str) and model.strip():
        return model.strip()
    return None


def _model_from_codex_record(record: dict[str, Any]) -> str | None:
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


def _model_from_record(record: dict[str, Any], *, agent: TranscriptAgent | None) -> str | None:
    if agent == "codex":
        return _model_from_codex_record(record)
    if agent == "claude":
        return _model_from_claude_record(record)
    if model := _model_from_codex_record(record):
        return model
    return _model_from_claude_record(record)


def _model_from_records(
    records: list[Any],
    *,
    agent: TranscriptAgent | None,
) -> str | None:
    for record in reversed(records):
        if not isinstance(record, dict):
            continue
        model = _model_from_record(record, agent=agent)
        if model:
            return model
    return None


def model_from_transcript(path: str) -> str | None:
    """Scan transcript jsonl backwards; return latest model when found (stdin/dev file read)."""
    transcript = Path(path).expanduser()
    if not transcript.is_file():
        return None

    try:
        records = _load_transcript_file(transcript)
    except OSError as exc:
        logger.debug("skills transcript read failed: %s", exc)
        return None

    agent = infer_transcript_agent(path)
    return _model_from_records(records, agent=agent)


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


def last_assistant_from_records(records: list[Any]) -> str | None:
    """Scan records backwards; return last assistant text when found."""
    fallback_text: str | None = None
    for record in reversed(records):
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


def last_assistant_from_transcript(path: str) -> str | None:
    """Scan transcript file backwards; return last assistant text when found."""
    transcript = Path(path).expanduser()
    if not transcript.is_file():
        return None

    try:
        records = _load_transcript_file(transcript)
    except OSError as exc:
        logger.debug("skills transcript read failed: %s", exc)
        return None

    return last_assistant_from_records(records)


def last_assistant_from_payload(
    payload: dict[str, Any],
    *,
    allow_file_read: bool,
) -> str | None:
    records = transcript_records_from_payload(payload, allow_file_read=allow_file_read)
    if not records:
        return None
    return last_assistant_from_records(records)


def resolve_model(
    payload: dict[str, Any],
    *,
    allow_file_read: bool,
) -> str | None:
    """Resolve model from hook payload, then transcript records when allowed."""
    if model := model_from_payload(payload):
        return model

    records = transcript_records_from_payload(payload, allow_file_read=allow_file_read)
    if not records:
        return None

    path = transcript_path_from_payload(payload)
    agent = infer_transcript_agent(path)
    return _model_from_records(records, agent=agent)


def hook_transcript_debug_details(
    payload: dict[str, Any],
    *,
    allow_file_read: bool,
) -> dict[str, Any]:
    path = transcript_path_from_payload(payload)
    return {
        "inferred_agent": infer_transcript_agent(path),
        "transcript_source": transcript_source_from_payload(
            payload,
            allow_file_read=allow_file_read,
        ),
        "resolved_model": resolve_model(payload, allow_file_read=allow_file_read),
    }


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


def skills_search_query_from_hook_payload(
    payload: dict[str, Any],
    *,
    allow_file_read: bool = True,
) -> str | None:
    """Build format_search_query(user, assistant) from hook stdin fields."""
    prompt = prompt_from_payload(payload)
    if not prompt:
        return None

    assistant = last_assistant_from_payload(payload, allow_file_read=allow_file_read)
    return format_search_query(prompt, assistant)
