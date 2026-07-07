"""Enrich hook payloads for cyt-client (stdlib only)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from cyt_client.rules_file import rules_file_path, workspace_root_from_payload
from cyt_client.skills import attach_client_skills

CYT_LAUNCH_AGENT_ENV = "CYT_LAUNCH_AGENT"
CYT_AGENT_FIELD = "cyt_agent"
CYT_RULES_INJECTION_FIELD = "cyt_rules_injection"


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
    env_value = os.environ.get(CYT_LAUNCH_AGENT_ENV, "").strip()
    if env_value:
        data[CYT_AGENT_FIELD] = env_value


def enrich_hook_payload(payload_bytes: bytes) -> bytes:
    """Attach ``cyt_agent``, ``cyt_transcript``, ``cyt_rules_injection``, and ``cyt_skills``."""
    if not payload_bytes.strip():
        return payload_bytes
    try:
        data = json.loads(payload_bytes)
    except json.JSONDecodeError:
        return payload_bytes
    if not isinstance(data, dict):
        return payload_bytes

    _attach_cyt_agent(data)

    transcript_path = _transcript_path_from_data(data)
    if transcript_path is not None:
        path = Path(transcript_path)
        if path.is_file():
            data["cyt_transcript"] = _load_transcript(path)

    _attach_cyt_rules_injection(data)
    attach_client_skills(data)

    return json.dumps(data, separators=(",", ":")).encode()
