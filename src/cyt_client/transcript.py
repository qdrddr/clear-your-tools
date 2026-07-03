"""Read hook transcript files for cyt-client (stdlib only)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


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


def enrich_hook_payload(payload_bytes: bytes) -> bytes:
    """Attach ``cyt_transcript`` when ``transcript_path`` points at a readable file."""
    if not payload_bytes.strip():
        return payload_bytes
    try:
        data = json.loads(payload_bytes)
    except json.JSONDecodeError:
        return payload_bytes
    if not isinstance(data, dict):
        return payload_bytes

    transcript_path = _transcript_path_from_data(data)
    if transcript_path is None:
        return payload_bytes

    path = Path(transcript_path)
    if not path.is_file():
        return payload_bytes

    data["cyt_transcript"] = _load_transcript(path)
    return json.dumps(data, separators=(",", ":")).encode()
