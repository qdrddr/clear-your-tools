"""Persist recently active consumer workspaces for language-agnostic CLI resolution."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

_ACTIVE_WORKSPACE_DIR = Path("~/.config/cyt/cache/active-workspaces").expanduser()
_ACTIVE_WORKSPACE_FILE = _ACTIVE_WORKSPACE_DIR / "by-agent.json"
_lock = threading.RLock()


def _normalize_agent(raw: object) -> str:
    text = str(raw or "cursor").strip().lower()
    return text if text in {"cursor", "claude", "codex"} else "cursor"


def _normalize_workspace_path(raw: object) -> str | None:
    from cyt.hook.catalog_registry import normalize_registry_workspace_path

    return normalize_registry_workspace_path(raw)


def _load_payload() -> dict[str, dict[str, int]]:
    if not _ACTIVE_WORKSPACE_FILE.is_file():
        return {}
    try:
        raw = json.loads(_ACTIVE_WORKSPACE_FILE.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    payload: dict[str, dict[str, int]] = {}
    for agent, rows in raw.items():
        if not isinstance(agent, str) or not isinstance(rows, dict):
            continue
        normalized: dict[str, int] = {}
        for path, seen_ms in rows.items():
            if isinstance(path, str) and isinstance(seen_ms, int):
                normalized[path] = seen_ms
        if normalized:
            payload[agent] = normalized
    return payload


def _write_payload(payload: dict[str, dict[str, int]]) -> None:
    target = _ACTIVE_WORKSPACE_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / f"{target.name}.{time.time_ns()}.tmp"
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(target)


def touch_active_workspace(
    agent: str,
    workspace_root: str | Path | None,
    *,
    seen_ms: int | None = None,
) -> None:
    """Record *workspace_root* as recently active for *agent*."""
    normalized = _normalize_workspace_path(workspace_root)
    if normalized is None:
        return
    now_ms = int(seen_ms if seen_ms is not None else time.time() * 1000)
    agent_key = _normalize_agent(agent)
    with _lock:
        payload = _load_payload()
        rows = dict(payload.get(agent_key, {}))
        rows[normalized] = now_ms
        payload[agent_key] = rows
        _write_payload(payload)


def list_active_workspaces(agent: str) -> list[dict[str, Any]]:
    """Return workspace roots for *agent*, newest first."""
    agent_key = _normalize_agent(agent)
    with _lock:
        rows = dict(_load_payload().get(agent_key, {}))
    return [
        {"root_path": path, "last_seen_ms": last_seen_ms}
        for path, last_seen_ms in sorted(
            rows.items(),
            key=lambda item: item[1],
            reverse=True,
        )
    ]


def resolve_recent_active_workspace(
    agent: str,
    *,
    exclude_roots: set[str] | None = None,
) -> Path | None:
    """Return the most recently active workspace, optionally excluding paths."""
    excluded = {str(item) for item in (exclude_roots or set())}
    for row in list_active_workspaces(agent):
        path = str(row.get("root_path") or "")
        if not path or path in excluded:
            continue
        try:
            resolved = Path(path).expanduser().resolve()
        except OSError:
            continue
        if resolved.is_dir():
            return resolved
    return None
