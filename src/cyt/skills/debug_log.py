"""Debug logging for agent hook invocations."""

from __future__ import annotations

import copy
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from cyt.skills.hook_payload import hook_event_name, session_id

_StdinDebugValue = dict[str, Any] | list[Any] | str | int | float | bool | None

_HOOKS_DEBUG_DIR = Path(".debug") / "hooks"
_FALLBACK_DEBUG_DIR = Path("~/.config/cyt/debug/hooks").expanduser()
_FILENAME_SAFE = re.compile(r"[^\w.-]+", re.ASCII)

# cyt-client enrichment fields (``cyt_*`` on the hook POST body).
_CYT_CLIENT_PAYLOAD_KEYS = {
    "cyt_agent": "agent",
    "cyt_skills": "skills",
    "cyt_transcript": "transcript",
    "cyt_rules_injection": "rules_injection",
}


def hooks_debug_dirs(cwd: str | None = None) -> list[Path]:
    """Project cwd from the hook payload, plus a stable user config fallback."""
    bases = [Path(cwd or os.getcwd()) / _HOOKS_DEBUG_DIR, _FALLBACK_DEBUG_DIR]
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

    hook.pop("cyt_hook_payload", None)

    for cyt_key, client_key in _CYT_CLIENT_PAYLOAD_KEYS.items():
        if cyt_key in hook:
            cyt_client[client_key] = hook.pop(cyt_key)

    for key in list(hook.keys()):
        if key.startswith("cyt_"):
            cyt_client[key[4:]] = hook.pop(key)

    return hook, cyt_client


def extract_hook_payload(request_payload: dict[str, Any]) -> dict[str, Any]:
    """Return the original agent hook payload from a captured request."""
    original = request_payload.get("cyt_hook_payload")
    if isinstance(original, dict):
        return copy.deepcopy(original)
    hook, _ = split_hook_and_cyt_client(request_payload)
    return hook


def payload_mutations(
    request_payload: dict[str, Any],
    server_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    """Describe fields added, removed, or changed by server-side normalization."""
    mutations: list[dict[str, Any]] = []
    all_keys = set(request_payload) | set(server_payload)
    for key in sorted(all_keys):
        before = request_payload.get(key, _MISSING)
        after = server_payload.get(key, _MISSING)
        if before is _MISSING:
            mutations.append({"field": key, "change": "added", "value": after})
        elif after is _MISSING:
            mutations.append({"field": key, "change": "removed", "value": before})
        elif before != after:
            mutations.append({"field": key, "change": "updated", "from": before, "to": after})
    return mutations


_MISSING = object()


def write_hook_debug_log(
    *,
    request_payload: dict[str, Any],
    server_payload: dict[str, Any] | None = None,
    cwd: str | None = None,
    skills_enabled: bool,
    tools_enabled: bool = False,
    outcome: str,
    details: dict[str, Any] | None = None,
) -> Path:
    """Write hook debug records under ``.debug/hooks`` and ``~/.config/cyt/debug/hooks``."""
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    normalized = server_payload if server_payload is not None else request_payload
    event = _safe_filename_part(hook_event_name(normalized) or "unknown", fallback="unknown")
    session = _safe_filename_part(
        session_id(normalized) or normalized.get("sessionId") or f"no-session-{timestamp}",
        fallback="no-session",
    )
    filename = f"{timestamp}_{event}_{session}.json"

    hook_payload = extract_hook_payload(request_payload)
    _, cyt_enrichment = split_hook_and_cyt_client(request_payload)
    cyt_client: dict[str, Any] = dict(cyt_enrichment)
    cyt_client["payload"] = copy.deepcopy(request_payload)
    cyt_client["skills_enabled"] = skills_enabled
    cyt_client["tools_enabled"] = tools_enabled
    cyt_client["outcome"] = outcome
    if details:
        cyt_client["injection"] = details

    server_block: dict[str, Any] = {"payload": copy.deepcopy(normalized)}
    mutations = payload_mutations(request_payload, normalized)
    if mutations:
        server_block["mutations"] = mutations

    entry: dict[str, Any] = {
        "timestamp": timestamp,
        "cwd": cwd or os.getcwd(),
        "stdin_raw": hook_payload,
        "cyt_client": cyt_client,
        "server": server_block,
    }
    body = json.dumps(entry, indent=2, default=str) + "\n"
    primary: Path | None = None
    for debug_dir in hooks_debug_dirs(cwd):
        debug_dir.mkdir(parents=True, exist_ok=True)
        path = debug_dir / filename
        path.write_text(body, encoding="utf-8")
        if primary is None:
            primary = path
    if primary is None:
        raise RuntimeError("no hook debug log directory")
    return primary


# Back-compat aliases (deprecated).
skills_debug_dirs = hooks_debug_dirs
write_skills_hook_debug_log = write_hook_debug_log
