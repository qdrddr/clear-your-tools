"""Tests for silent hook behavior when MCPC is unavailable."""

from __future__ import annotations

from unittest.mock import patch

from cyt.tools.hook import handle_user_prompt_tools

_MCP_CONFIG = {
    "skills": {"enabled": False},
    "pruning": {
        "inject_via": "hook",
        "tools": {
            "enabled": True,
            "hook": {
                "tools_from": "mcpc",
                "mcpc": {"executable": "mcpc"},
            },
            "sequence": ["bm25"],
        },
    },
}


def test_hook_skips_tools_silently_when_mcpc_unavailable() -> None:
    payload = {
        "hook_event_name": "UserPromptSubmit",
        "prompt": "find docs",
        "cwd": "/tmp/project",
    }
    with patch("cyt.tools.hook.mcpc_hook_catalog_usable", return_value=False):
        outcome, details, injected = handle_user_prompt_tools(payload, _MCP_CONFIG)

    assert outcome == "skipped_mcpc_unavailable"
    assert details == {}
    assert injected == ""


def test_hook_skips_tools_silently_when_mcpc_sessions_empty() -> None:
    payload = {
        "hook_event_name": "UserPromptSubmit",
        "prompt": "find docs",
        "cwd": "/tmp/project",
    }
    with patch("cyt.mcpc.readiness.probe_mcpc_sessions", return_value="empty"):
        outcome, details, injected = handle_user_prompt_tools(payload, _MCP_CONFIG)

    assert outcome == "skipped_mcpc_unavailable"
    assert details == {}
    assert injected == ""
