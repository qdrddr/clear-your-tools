"""Extract assistant context from session transcript jsonl (orchestrator)."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Literal, cast

from cyt.agents._types import CYT_AGENT_FIELD, CYT_LAUNCH_AGENT_ENV
from cyt.proxy.anthropic import format_search_query
from cyt.skills.hook_payload import model_from_payload, prompt_from_payload

logger = logging.getLogger(__name__)

TranscriptAgent = Literal["claude", "codex", "cursor"]
TranscriptSource = Literal["inline", "file", "none"]


def transcript_path_from_payload(payload: dict[str, Any]) -> str | None:
    raw = payload.get("transcript_path")
    if raw is None:
        return None
    if not isinstance(raw, str):
        return None
    path = raw.strip()
    return path or None


def _agent_from_payload(payload: dict[str, Any], path: str | None) -> TranscriptAgent | None:
    raw = payload.get(CYT_AGENT_FIELD)
    if isinstance(raw, str) and raw.strip():
        agent = raw.strip().lower()
        if agent in ("claude", "codex", "cursor"):
            return cast(TranscriptAgent, agent)
    return infer_transcript_agent(path)


def infer_transcript_agent(path: str | None) -> TranscriptAgent | None:
    """Infer agent from transcript_path heuristics or CYT_LAUNCH_AGENT."""
    if path:
        normalized = path.replace("\\", "/").lower()
        if "/.codex/" in normalized or normalized.startswith("~/.codex/"):
            return "codex"
        if "/.claude/" in normalized or normalized.startswith("~/.claude/"):
            return "claude"
        if "/.cursor/projects/" in normalized or "/agent-transcripts/" in normalized:
            return "cursor"

    env_value = os.environ.get(CYT_LAUNCH_AGENT_ENV)
    if isinstance(env_value, str):
        agent = env_value.strip().lower()
        if agent in ("claude", "codex"):
            return cast(TranscriptAgent, agent)
        if agent == "cursor" and path is None:
            return "cursor"
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


def _resolve_transcript_agent(
    payload: dict[str, Any],
    *,
    allow_file_read: bool,
) -> TranscriptAgent | None:
    path = transcript_path_from_payload(payload)
    agent = _agent_from_payload(payload, path)
    if agent is not None:
        return agent
    if allow_file_read and path:
        return infer_transcript_agent(path)
    return None


def _parse_last_assistant(records: list[Any], agent: TranscriptAgent | None) -> str | None:
    if agent is None:
        from cyt.agents.claude.skills_hook import last_assistant_from_records as claude_last
        from cyt.agents.codex.skills_hook import last_assistant_from_records as codex_last

        text = codex_last(records)
        if text:
            return text
        return claude_last(records)

    from cyt.agents._registry import get_agent

    cap = get_agent(agent).skills_hook
    if cap.parse_last_assistant is None:
        return None
    return cap.parse_last_assistant(records)


def _model_from_records(records: list[Any], *, agent: TranscriptAgent | None) -> str | None:
    if agent is None:
        from cyt.agents.claude.skills_hook import model_from_records as claude_model
        from cyt.agents.codex.skills_hook import model_from_records as codex_model

        if model := codex_model(records):
            return model
        return claude_model(records)

    from cyt.agents._registry import get_agent

    cap = get_agent(agent).skills_hook
    if cap.parse_model_from_records is None:
        return None
    return cap.parse_model_from_records(records)


def model_from_transcript(path: str) -> str | None:
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


def last_assistant_from_records(
    records: list[Any],
    *,
    agent: TranscriptAgent | None = None,
) -> str | None:
    return _parse_last_assistant(records, agent)


def last_assistant_from_transcript(path: str) -> str | None:
    transcript = Path(path).expanduser()
    if not transcript.is_file():
        return None

    try:
        records = _load_transcript_file(transcript)
    except OSError as exc:
        logger.debug("skills transcript read failed: %s", exc)
        return None

    agent = infer_transcript_agent(path)
    return _parse_last_assistant(records, agent)


def last_assistant_from_payload(
    payload: dict[str, Any],
    *,
    allow_file_read: bool,
) -> str | None:
    records = transcript_records_from_payload(payload, allow_file_read=allow_file_read)
    if not records:
        return None
    agent = _resolve_transcript_agent(payload, allow_file_read=allow_file_read)
    return _parse_last_assistant(records, agent)


def resolve_model(
    payload: dict[str, Any],
    *,
    allow_file_read: bool,
) -> str | None:
    if model := model_from_payload(payload):
        return model

    records = transcript_records_from_payload(payload, allow_file_read=allow_file_read)
    if not records:
        return None

    agent = _resolve_transcript_agent(payload, allow_file_read=allow_file_read)
    return _model_from_records(records, agent=agent)


def hook_transcript_debug_details(
    payload: dict[str, Any],
    *,
    allow_file_read: bool,
) -> dict[str, Any]:
    path = transcript_path_from_payload(payload)
    return {
        "inferred_agent": _agent_from_payload(payload, path) or infer_transcript_agent(path),
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
    prompt = prompt_from_payload(payload)
    if not prompt:
        return None

    assistant = last_assistant_from_payload(payload, allow_file_read=allow_file_read)
    return format_search_query(prompt, assistant)
