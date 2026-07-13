"""Normalize agent hook stdin JSON."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cyt.agents._types import CYT_AGENT_FIELD


def _merge_hook_payload_data(data: dict[str, Any]) -> dict[str, Any]:
    nested = data.get("payload")
    if isinstance(nested, dict):
        merged = dict(nested)
        for key, value in data.items():
            if key != "payload":
                merged[key] = value
        return merged
    return dict(data)


def normalize_hook_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Merge nested ``payload`` fields and dispatch agent-specific normalize."""
    merged = _merge_hook_payload_data(data)
    agent = merged.get(CYT_AGENT_FIELD)
    if agent == "cursor":
        from cyt.agents.cursor.skills_hook import normalize_cursor_payload

        return normalize_cursor_payload(merged)

    from cyt.agents.cursor.skills_hook import looks_like_cursor_hook

    if looks_like_cursor_hook(merged):
        from cyt.agents.cursor.skills_hook import normalize_cursor_payload

        return normalize_cursor_payload(merged)
    return merged


def hook_event_name(payload: dict[str, Any]) -> str | None:
    name = payload.get("hook_event_name") or payload.get("hookEventName")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def hook_cwd(payload: dict[str, Any]) -> str | None:
    cwd = payload.get("cwd")
    return cwd if isinstance(cwd, str) and cwd.strip() else None


def session_id(payload: dict[str, Any]) -> str | None:
    session_id = payload.get("session_id") or payload.get("sessionId")
    if isinstance(session_id, str) and session_id.strip():
        return session_id.strip()
    return None


def model_from_payload(payload: dict[str, Any]) -> str | None:
    model = payload.get("model")
    if isinstance(model, str) and model.strip():
        return model.strip()
    return None


def prompt_from_payload(payload: dict[str, Any]) -> str | None:
    prompt = payload.get("prompt")
    if isinstance(prompt, str) and prompt.strip():
        return prompt.strip()
    return None


def _normalized_path_key(path: str) -> Path:
    return Path(path.strip()).expanduser()


def _append_workspace_path(candidates: list[str], seen: set[Path], raw: str) -> None:
    stripped = raw.strip()
    if not stripped:
        return
    key = _normalized_path_key(stripped)
    if key in seen:
        return
    seen.add(key)
    candidates.append(stripped)


def workspace_paths_for_tools_inject(payload: dict[str, Any]) -> list[str]:
    """Merge hook ``workspace_roots``, ``cwd``, and ``cyt.cwd`` into a deduplicated path list."""
    candidates: list[str] = []
    seen: set[Path] = set()

    roots = payload.get("workspace_roots")
    if isinstance(roots, list):
        for root in roots:
            if isinstance(root, str):
                _append_workspace_path(candidates, seen, root)

    cwd = payload.get("cwd")
    if isinstance(cwd, str):
        _append_workspace_path(candidates, seen, cwd)

    cyt = payload.get("cyt")
    if isinstance(cyt, dict):
        cyt_cwd = cyt.get("cwd")
        if isinstance(cyt_cwd, str):
            _append_workspace_path(candidates, seen, cyt_cwd)

    return candidates
