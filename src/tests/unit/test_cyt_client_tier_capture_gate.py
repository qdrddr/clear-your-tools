"""Tests that cyt-client tier feedback respects capture.source config."""

from __future__ import annotations

from unittest.mock import patch

from cyt_client.cli import _handle_post_tool_capture, _handle_pre_tool


def test_pre_tool_skips_tier_feedback_when_cyt_mcp_capture() -> None:
    payload = {
        "hook_event_name": "preToolUse",
        "tool_name": "MCP:grep",
        "cwd": "/tmp",
    }
    with patch("cyt_client.cli.validate_pre_tool_call") as validate, patch(
        "cyt_client.config.tier_tool_capture_via_hooks",
        return_value=False,
    ), patch("cyt_client.tier_feedback.notify_tool_used_feedback") as notify:
        validate.return_value = type(
            "R",
            (),
            {"allowed": True, "reason": "", "exposure": None},
        )()
        _handle_pre_tool(payload, cursor_output=False)
        notify.assert_not_called()


def test_post_tool_skips_tier_feedback_when_cyt_mcp_capture() -> None:
    payload = {
        "hook_event_name": "postToolUse",
        "tool_name": "MCP:grep",
        "cwd": "/tmp",
    }
    with patch("cyt_client.config.tier_tool_capture_via_hooks", return_value=False), patch(
        "cyt_client.tier_feedback.notify_tool_used_feedback",
    ) as notify:
        _handle_post_tool_capture(payload, cursor_output=False)
        notify.assert_not_called()
