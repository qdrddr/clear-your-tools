"""Build searchable session text corpora for the pre-exposed gate."""

from __future__ import annotations

from typing import Any, Literal, cast

from cyt.proxy.user_message_inject import _message_content_text
from cyt.skills.transcript import (
    TranscriptAgent,
    transcript_records_from_payload,
)

ProxyKind = Literal["anthropic", "openai"]


def _append_part(parts: list[str], text: str | None) -> None:
    if text and text.strip():
        parts.append(text.strip())


def _text_from_content_value(content: object) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    segments: list[str] = []
    for block_obj in content:
        if not isinstance(block_obj, dict):
            continue
        block = cast(dict[str, Any], block_obj)
        block_type = block.get("type")
        if block_type in ("input_text", "output_text", "text"):
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                segments.append(text.strip())
        elif block_type == "thinking":
            thinking = block.get("thinking")
            if isinstance(thinking, str) and thinking.strip():
                segments.append(thinking.strip())
    return "\n".join(segments)


def _anthropic_system_text(system: object) -> str:
    if isinstance(system, str):
        return system.strip()
    if isinstance(system, list):
        return _text_from_content_value(system)
    return ""


def _session_text_from_anthropic_body(body: dict[str, Any]) -> str:
    parts: list[str] = []
    system = body.get("system")
    if system is not None:
        _append_part(parts, _anthropic_system_text(system))
    messages = body.get("messages") or []
    if isinstance(messages, list):
        for message in messages:
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if isinstance(content, str):
                _append_part(parts, content)
            else:
                _append_part(parts, _text_from_content_value(content))
    return "\n".join(parts)


def _session_text_from_openai_body(body: dict[str, Any]) -> str:
    parts: list[str] = []
    instructions = body.get("instructions")
    if isinstance(instructions, str):
        _append_part(parts, instructions)
    input_items = body.get("input") or []
    if isinstance(input_items, list):
        for item in input_items:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if isinstance(content, str):
                _append_part(parts, content)
            else:
                _append_part(parts, _message_content_text(content))
    return "\n".join(parts)


def session_text_from_proxy_body(body: dict[str, Any], kind: ProxyKind) -> str:
    """Concatenate message/system text from an upstream proxy request body."""
    if kind == "anthropic":
        return _session_text_from_anthropic_body(body)
    return _session_text_from_openai_body(body)


def _all_turns_text_from_records(records: list[Any], agent: TranscriptAgent | None) -> str:
    if agent == "cursor":
        from cyt.agents.cursor.skills_hook import all_turns_text_from_records

        return all_turns_text_from_records(records)
    if agent == "codex":
        from cyt.agents.codex.skills_hook import all_turns_text_from_records

        return all_turns_text_from_records(records)
    if agent == "claude":
        from cyt.agents.claude.skills_hook import all_turns_text_from_records

        return all_turns_text_from_records(records)

    from cyt.agents.claude.skills_hook import all_turns_text_from_records as claude_all
    from cyt.agents.codex.skills_hook import all_turns_text_from_records as codex_all
    from cyt.agents.cursor.skills_hook import all_turns_text_from_records as cursor_all

    for collector in (cursor_all, codex_all, claude_all):
        text = collector(records)
        if text.strip():
            return text
    return ""


def _resolve_transcript_agent_for_payload(
    payload: dict[str, Any],
    *,
    allow_file_read: bool,
) -> TranscriptAgent | None:
    from cyt.skills.transcript import _resolve_transcript_agent

    return _resolve_transcript_agent(payload, allow_file_read=allow_file_read)


def _rules_injection_from_payload(payload: dict[str, Any]) -> str:
    raw = payload.get("cyt_rules_injection")
    if isinstance(raw, str):
        return raw.strip()
    return ""


def session_text_from_hook_payload(
    payload: dict[str, Any],
    *,
    allow_file_read: bool = True,
) -> str:
    """Concatenate transcript turns and optional Cursor rules injection body."""
    parts: list[str] = []
    records = transcript_records_from_payload(payload, allow_file_read=allow_file_read)
    if records:
        agent = _resolve_transcript_agent_for_payload(payload, allow_file_read=allow_file_read)
        _append_part(parts, _all_turns_text_from_records(records, agent))
    _append_part(parts, _rules_injection_from_payload(payload))
    return "\n".join(parts)
