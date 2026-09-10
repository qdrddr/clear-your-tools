"""Unit tests for tool examples hook wizard integration."""

from __future__ import annotations

from cyt.hook.setup_wizard import _cursor_post_tool_matcher
from cyt_client.hook_invocation import (
    CURSOR_POST_TOOL_DEFINITIONS_MATCHER,
    CURSOR_POST_TOOL_EXAMPLES_MATCHER,
)


def test_post_tool_matcher_definitions_only_when_examples_disabled() -> None:
    matcher = _cursor_post_tool_matcher({"tools": {"examples": {"enabled": False}}})
    assert matcher == CURSOR_POST_TOOL_DEFINITIONS_MATCHER
    assert "mcp__.*" not in matcher


def test_post_tool_matcher_uses_config_when_examples_enabled() -> None:
    matcher = _cursor_post_tool_matcher(
        {
            "tools": {
                "examples": {
                    "enabled": True,
                    "capture": {"post_tool_matcher": "custom:.*"},
                },
            },
        },
    )
    assert CURSOR_POST_TOOL_DEFINITIONS_MATCHER in matcher
    assert "custom:.*" in matcher


def test_post_tool_matcher_defaults_examples_pattern() -> None:
    matcher = _cursor_post_tool_matcher({"tools": {"examples": {"enabled": True}}})
    assert "MCP:*" in matcher
    assert "mcp__*" in matcher
