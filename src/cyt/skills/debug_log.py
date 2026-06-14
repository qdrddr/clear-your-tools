"""Debug logging for `cyt hook --stdin` agent hooks."""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cyt.skills.hook_payload import hook_event_name, session_id

_SKILLS_DEBUG_DIR = Path(".debug") / "skills"
_FALLBACK_DEBUG_DIR = Path("~/.config/cyt/debug/skills").expanduser()
_FILENAME_SAFE = re.compile(r"[^\w.-]+", re.ASCII)


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


def write_skills_hook_debug_log(
    *,
    raw_stdin: str,
    payload: dict[str, Any],
    cwd: str | None = None,
    skills_enabled: bool,
    outcome: str,
    details: dict[str, Any] | None = None,
) -> Path:
    """Write hook stdin and handling outcome under ``.debug/skills`` and ``~/.config/cyt/debug/skills``."""
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    event = _safe_filename_part(hook_event_name(payload) or "unknown", fallback="unknown")
    session = _safe_filename_part(session_id(payload) or "no-session", fallback="no-session")
    filename = f"{timestamp}_{event}_{session}.json"
    entry: dict[str, Any] = {
        "timestamp": timestamp,
        "cwd": cwd or os.getcwd(),
        "stdin_raw": raw_stdin,
        "payload": payload,
        "skills_enabled": skills_enabled,
        "outcome": outcome,
    }
    if details:
        entry["details"] = details
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
