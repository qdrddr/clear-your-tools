"""Debug logging for `cyt hook --stdin` agent hooks."""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from cyt.skills.hook_payload import hook_event_name, session_id

_StdinDebugValue = dict[str, Any] | list[Any] | str | int | float | bool | None

_SKILLS_DEBUG_DIR = Path(".debug") / "skills"
_FALLBACK_DEBUG_DIR = Path("~/.config/cyt/debug/skills").expanduser()
_FILENAME_SAFE = re.compile(r"[^\w.-]+", re.ASCII)

# cyt-client enrichment fields (``cyt_*`` on the hook POST body).
_CYT_CLIENT_PAYLOAD_KEYS = {
    "cyt_agent": "agent",
    "cyt_skills": "skills",
    "cyt_transcript": "transcript",
    "cyt_rules_injection": "rules_injection",
}


def skills_debug_dirs(cwd: str | None = None) -> list[Path]:
    """Project cwd from the hook payload, plus a stable user config fallback."""
    bases = [Path(cwd or os.getcwd()) / _SKILLS_DEBUG_DIR, _FALLBACK_DEBUG_DIR]
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in bases:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(path)
    return unique


def _safe_filename_part(value: str, *, fallback: str) -> str:
    cleaned = _FILENAME_SAFE.sub("_", value.strip())
    return cleaned or fallback


def _stdin_debug_value(raw_stdin: str) -> _StdinDebugValue:
    """Return hook stdin as a JSON value for debug files (not an escaped string)."""
    stripped = raw_stdin.strip()
    if not stripped:
        return None
    try:
        return cast(_StdinDebugValue, json.loads(stripped))
    except json.JSONDecodeError:
        return {"_unparsed": raw_stdin}


def split_hook_and_cyt_client(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Separate the agent hook payload from cyt-client enrichments."""
    hook = dict(data)
    cyt_client: dict[str, Any] = {}

    for cyt_key, client_key in _CYT_CLIENT_PAYLOAD_KEYS.items():
        if cyt_key in hook:
            cyt_client[client_key] = hook.pop(cyt_key)

    for key in list(hook.keys()):
        if key.startswith("cyt_"):
            cyt_client[key[4:]] = hook.pop(key)

    return hook, cyt_client


def write_skills_hook_debug_log(
    *,
    raw_stdin: str,
    payload: dict[str, Any],
    cwd: str | None = None,
    skills_enabled: bool,
    tools_enabled: bool = False,
    outcome: str,
    details: dict[str, Any] | None = None,
) -> Path:
    """Write hook stdin and handling outcome under ``.debug/skills`` and ``~/.config/cyt/debug/skills``."""
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    event = _safe_filename_part(hook_event_name(payload) or "unknown", fallback="unknown")
    session = _safe_filename_part(
        session_id(payload) or payload.get("sessionId") or f"no-session-{timestamp}",
        fallback="no-session",
    )
    filename = f"{timestamp}_{event}_{session}.json"

    stdin_value = _stdin_debug_value(raw_stdin)
    hook_payload: _StdinDebugValue = stdin_value
    cyt_client: dict[str, Any] = {}
    if isinstance(stdin_value, dict):
        hook_payload, cyt_client = split_hook_and_cyt_client(stdin_value)

    cyt_client["skills_enabled"] = skills_enabled
    cyt_client["tools_enabled"] = tools_enabled
    cyt_client["outcome"] = outcome
    if details:
        cyt_client["injection"] = details

    entry: dict[str, Any] = {
        "timestamp": timestamp,
        "cwd": cwd or os.getcwd(),
        "stdin_raw": hook_payload,
        "cyt_client": cyt_client,
    }
    body = json.dumps(entry, indent=2, default=str) + "\n"
    primary: Path | None = None
    for debug_dir in skills_debug_dirs(cwd):
        debug_dir.mkdir(parents=True, exist_ok=True)
        path = debug_dir / filename
        path.write_text(body, encoding="utf-8")
        if primary is None:
            primary = path
    if primary is None:
        raise RuntimeError("no skills debug log directory")
    return primary
