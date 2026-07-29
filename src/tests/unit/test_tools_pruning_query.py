"""Tests for hook-gated tools pruning query augmentation."""

from __future__ import annotations

from typing import Any

from cyt.pruners.query import (
    TOOLS_HOOK_OPTIONAL_SCOPE_INSTRUCTION,
    tools_pruning_query,
)

_HOOK_CONFIG: dict[str, Any] = {
    "pruning": {
        "inject_via": "hook",
        "tools": {"enabled": True},
    },
}

_PROXY_CONFIG: dict[str, Any] = {
    "pruning": {
        "inject_via": "proxy",
        "tools": {"enabled": True},
    },
}

_DISABLED_TOOLS_HOOK_CONFIG: dict[str, Any] = {
    "pruning": {
        "inject_via": "hook",
        "tools": {"enabled": False},
    },
}


def test_tools_pruning_query_appends_instruction_for_hook() -> None:
    query = "find my calendar events"
    result = tools_pruning_query(query, _HOOK_CONFIG)
    assert result.startswith(query)
    assert result.endswith(TOOLS_HOOK_OPTIONAL_SCOPE_INSTRUCTION)


def test_tools_pruning_query_unchanged_for_proxy() -> None:
    query = "find my calendar events"
    assert tools_pruning_query(query, _PROXY_CONFIG) == query


def test_tools_pruning_query_unchanged_when_tools_disabled() -> None:
    query = "find my calendar events"
    assert tools_pruning_query(query, _DISABLED_TOOLS_HOOK_CONFIG) == query


def test_tools_pruning_query_unchanged_for_empty_query() -> None:
    assert tools_pruning_query("", _HOOK_CONFIG) == ""
    assert tools_pruning_query("   ", _HOOK_CONFIG) == "   "
