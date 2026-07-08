"""Detect agent harness from hook payload and environment (stdlib only)."""

from __future__ import annotations

import os
from typing import Any

CODEX_HOME_ENV = "CODEX_HOME"
CURSOR_VERSION_ENV = "CURSOR_VERSION"
CLAUDE_PROJECT_DIR_ENV = "CLAUDE_PROJECT_DIR"
CLAUDECODE_ENV = "CLAUDECODE"
CLAUDE_CODE_ENTRYPOINT_ENV = "CLAUDE_CODE_ENTRYPOINT"
CYT_LAUNCH_AGENT_ENV = "CYT_LAUNCH_AGENT"

_KNOWN_AGENTS = frozenset({"claude", "codex", "cursor"})
_CURSOR_BEFORE_SUBMIT_EVENT = "beforeSubmitPrompt"


def _non_empty_env(name: str) -> bool:
    return bool(os.environ.get(name, "").strip())


def _payload_layers(data: dict[str, Any]) -> list[dict[str, Any]]:
    layers = [data]
    nested = data.get("payload")
    if isinstance(nested, dict):
        layers.append(nested)
    return layers


def _hook_event_name(data: dict[str, Any]) -> str | None:
    name = data.get("hook_event_name") or data.get("hookEventName")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def _cursor_version_in_payload(data: dict[str, Any]) -> bool:
    raw = data.get("cursor_version")
    return isinstance(raw, str) and bool(raw.strip())


def _before_submit_prompt_in_payload(data: dict[str, Any]) -> bool:
    return _hook_event_name(data) == _CURSOR_BEFORE_SUBMIT_EVENT


def _transcript_path_from_layer(data: dict[str, Any]) -> str | None:
    raw = data.get("transcript_path")
    if isinstance(raw, str):
        path = raw.strip()
        if path:
            return path
    return None


def _transcript_path_from_data(data: dict[str, Any]) -> str | None:
    for layer in _payload_layers(data):
        if path := _transcript_path_from_layer(layer):
            return path
    return None


def _agent_from_transcript_path(path: str) -> str | None:
    normalized = path.replace("\\", "/").lower()
    if "/.codex/" in normalized or normalized.endswith("/.codex"):
        return "codex"
    if "/.claude/" in normalized or normalized.endswith("/.claude"):
        return "claude"
    if "/.cursor/projects/" in normalized:
        return "cursor"
    return None


def looks_like_cursor_payload(data: dict[str, Any]) -> bool:
    """True when tier-1/2 cursor harness detection signals match."""
    if _non_empty_env(CURSOR_VERSION_ENV):
        return True
    for layer in _payload_layers(data):
        if _cursor_version_in_payload(layer) or _before_submit_prompt_in_payload(layer):
            return True
    return False


def infer_harness_agent(data: dict[str, Any]) -> str | None:
    """Infer agent harness from env and payload signals."""
    if _non_empty_env(CODEX_HOME_ENV):
        return "codex"
    if looks_like_cursor_payload(data):
        return "cursor"
    if _non_empty_env(CLAUDE_PROJECT_DIR_ENV):
        return "claude"
    if _non_empty_env(CLAUDECODE_ENV) or _non_empty_env(CLAUDE_CODE_ENTRYPOINT_ENV):
        return "claude"

    if path := _transcript_path_from_data(data):
        if agent := _agent_from_transcript_path(path):
            return agent

    launch_agent = os.environ.get(CYT_LAUNCH_AGENT_ENV, "").strip().lower()
    if launch_agent in _KNOWN_AGENTS:
        return launch_agent
    return None
