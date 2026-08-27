"""Enrich hook payloads for cyt-client (stdlib only)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, cast

from cyt_client.agent import infer_harness_agent
from cyt_client.rules_file import (
    rules_file_path,
    workspace_path_string,
    workspace_root_from_payload,
)
from cyt_client.sessions import read_tool_catalog_hashes, session_log_path
from cyt_client.skills import attach_client_skills

CYT_AGENT_FIELD = "cyt_agent"
CYT_HOOK_PAYLOAD_FIELD = "cyt_hook_payload"
CYT_RULES_INJECTION_FIELD = "cyt_rules_injection"
CYT_SESSION_LOG_FIELD = "cyt_session_log"
CYT_SESSION_AGENT_FIELD = "cyt_session_agent"


def _transcript_path_from_data(data: dict[str, Any]) -> str | None:
    nested = data.get("payload")
    if isinstance(nested, dict):
        raw = nested.get("transcript_path")
        if isinstance(raw, str):
            path = raw.strip()
            if path:
                return path
    raw = data.get("transcript_path")
    if isinstance(raw, str):
        path = raw.strip()
        if path:
            return path
    return None


def _load_transcript(path: Path) -> list[Any]:
    text = path.read_text(encoding="utf-8")
    try:
        return [json.loads(text)]
    except json.JSONDecodeError:
        pass

    items: list[Any] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            items.append(json.loads(stripped))
        except json.JSONDecodeError:
            continue
    if items:
        return items

    return [text]


def _strip_rules_mdc_frontmatter(content: str) -> str:
    text = content.lstrip()
    if not text.startswith("---"):
        return content.strip()
    end = text.find("\n---", 3)
    if end == -1:
        return content.strip()
    body_start = end + 4
    return text[body_start:].lstrip("\n").strip()


def _attach_cyt_rules_injection(data: dict[str, Any]) -> bool:
    workspace = workspace_root_from_payload(data)
    if workspace is None:
        return False
    path = rules_file_path(workspace)
    if not path.is_file():
        return False
    body = _strip_rules_mdc_frontmatter(path.read_text(encoding="utf-8"))
    if not body:
        return False
    data[CYT_RULES_INJECTION_FIELD] = body
    return True


def _attach_cyt_agent(data: dict[str, Any]) -> None:
    if agent := infer_harness_agent(data):
        data[CYT_AGENT_FIELD] = agent


def _attach_cyt_cwd(data: dict[str, Any]) -> None:
    workspace = workspace_root_from_payload(data)
    if workspace is None:
        return
    cyt = data.get("cyt")
    if not isinstance(cyt, dict):
        cyt = {}
        data["cyt"] = cyt
    existing = cyt.get("cwd")
    if isinstance(existing, str) and existing.strip():
        return
    cyt["cwd"] = workspace_path_string(data) or str(workspace)


def enrich_hook_payload(
    payload_bytes: bytes,
    *,
    rules_injection: str | None = None,
) -> bytes:
    """Attach ``cyt_hook_payload``, ``cyt_agent``, ``cyt.cwd``, ``cyt_transcript``, ``cyt_rules_injection``, and ``cyt_skills``."""
    if not payload_bytes.strip():
        return payload_bytes
    try:
        data = json.loads(payload_bytes)
    except json.JSONDecodeError:
        return payload_bytes
    if not isinstance(data, dict):
        return payload_bytes

    data[CYT_HOOK_PAYLOAD_FIELD] = copy.deepcopy(data)

    _attach_cyt_agent(data)
    _attach_cyt_cwd(data)

    transcript_path = _transcript_path_from_data(data)
    if transcript_path is not None:
        path = Path(transcript_path)
        if path.is_file():
            data["cyt_transcript"] = _load_transcript(path)

    if rules_injection is not None:
        if rules_injection.strip():
            data[CYT_RULES_INJECTION_FIELD] = rules_injection.strip()
    else:
        _attach_cyt_rules_injection(data)
    attach_client_skills(data)
    _attach_cyt_session_log(data)

    return json.dumps(data, separators=(",", ":")).encode()


def _attach_cyt_session_log(data: dict[str, Any]) -> None:
    path = session_log_path(data)
    if path is None or not path.is_file():
        return
    from cyt_client.sessions import entries_after_latest_compaction, read_session_log_file

    agent, items = read_session_log_file(path)
    items = entries_after_latest_compaction(items)
    filtered: list[dict[str, Any]] = []
    for item in items:
        if item.get("kind") == "tool_catalog":
            continue
        filtered.append(item)
    if filtered:
        data[CYT_SESSION_LOG_FIELD] = filtered
    catalog_hashes = read_tool_catalog_hashes(path)
    if catalog_hashes:
        data["tool_catalog_hashes"] = catalog_hashes
    if agent:
        data[CYT_SESSION_AGENT_FIELD] = agent


def prompt_from_payload(payload: dict[str, Any]) -> str | None:
    for layer in (
        payload,
        payload.get("payload") if isinstance(payload.get("payload"), dict) else {},
    ):
        if not isinstance(layer, dict):
            continue
        prompt = layer.get("prompt")
        if isinstance(prompt, str) and prompt.strip():
            return prompt.strip()
    return None


def _text_blocks_from_content(content: object) -> str | None:
    if isinstance(content, str) and content.strip():
        return content.strip()
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_dict = cast(dict[str, Any], block)
        block_type = block_dict.get("type")
        if block_type not in ("text", "output_text", "input_text"):
            continue
        text = block_dict.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    return "\n".join(parts) if parts else None


def _cursor_assistant_from_record(record: dict[str, Any]) -> str | None:
    if record.get("role") != "assistant":
        return None
    message = record.get("message")
    if not isinstance(message, dict):
        return None
    return _text_blocks_from_content(message.get("content"))


def _claude_assistant_from_record(record: dict[str, Any]) -> str | None:
    message = record.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        return None
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    return _text_blocks_from_content(content)


def _codex_assistant_from_record(record: dict[str, Any]) -> str | None:
    if record.get("type") != "response_item":
        return None
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None
    if payload.get("type") != "message" or payload.get("role") != "assistant":
        return None
    return _text_blocks_from_content(payload.get("content"))


def _cursor_user_from_record(record: dict[str, Any]) -> str | None:
    if record.get("role") != "user":
        return None
    message = record.get("message")
    if not isinstance(message, dict):
        return None
    return _text_blocks_from_content(message.get("content"))


def _claude_user_from_record(record: dict[str, Any]) -> str | None:
    message = record.get("message")
    if not isinstance(message, dict) or message.get("role") != "user":
        return None
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    return _text_blocks_from_content(content)


def _codex_user_from_record(record: dict[str, Any]) -> str | None:
    if record.get("type") != "response_item":
        return None
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None
    if payload.get("type") != "message" or payload.get("role") != "user":
        return None
    return _text_blocks_from_content(payload.get("content"))


def last_user_from_records(
    records: list[Any],
    *,
    agent: str | None = None,
) -> str | None:
    parser = {
        "cursor": _cursor_user_from_record,
        "claude": _claude_user_from_record,
        "codex": _codex_user_from_record,
    }.get(agent or "")
    for record in reversed(records):
        if not isinstance(record, dict):
            continue
        if parser is not None:
            if text := parser(record):
                return text
            continue
        for fallback in (
            _codex_user_from_record,
            _claude_user_from_record,
            _cursor_user_from_record,
        ):
            if text := fallback(record):
                return text
    return None


def transcript_records_from_payload(payload: dict[str, Any]) -> list[Any]:
    records = payload.get("cyt_transcript")
    if isinstance(records, list) and records:
        return records
    transcript_path = _transcript_path_from_data(payload)
    if transcript_path is None:
        return []
    path = Path(transcript_path)
    if not path.is_file():
        return []
    return _load_transcript(path)


def last_user_from_payload(payload: dict[str, Any]) -> str | None:
    records = transcript_records_from_payload(payload)
    if not records:
        return None
    agent = infer_harness_agent(payload)
    return last_user_from_records(records, agent=agent)


def last_turn_query_from_payload(payload: dict[str, Any]) -> str | None:
    records = transcript_records_from_payload(payload)
    if not records:
        return None
    agent = infer_harness_agent(payload)
    user = last_user_from_records(records, agent=agent)
    if not user:
        return None
    assistant = last_assistant_from_records(records, agent=agent)
    base = f"User_Asks: {user}"
    if assistant:
        return f"{base}; Assistant_Says: {assistant}"
    return base


def last_assistant_from_records(
    records: list[Any],
    *,
    agent: str | None = None,
) -> str | None:
    parser = {
        "cursor": _cursor_assistant_from_record,
        "claude": _claude_assistant_from_record,
        "codex": _codex_assistant_from_record,
    }.get(agent or "")
    for record in reversed(records):
        if not isinstance(record, dict):
            continue
        if parser is not None:
            if text := parser(record):
                return text
            continue
        for fallback in (
            _codex_assistant_from_record,
            _claude_assistant_from_record,
            _cursor_assistant_from_record,
        ):
            if text := fallback(record):
                return text
    return None


def last_assistant_from_payload(payload: dict[str, Any]) -> str | None:
    records = payload.get("cyt_transcript")
    if not isinstance(records, list) or not records:
        return None
    agent = infer_harness_agent(payload)
    return last_assistant_from_records(records, agent=agent)
