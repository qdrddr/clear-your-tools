"""Tests for shared token stat formatters."""

from __future__ import annotations

from cyt.pruners.token_stats import (
    build_preview_token_stats,
    format_agent_tools_token_lines,
    format_preview_token_summary_lines,
    format_tool_token_arrow_line,
    format_tool_token_line,
)


def test_format_tool_token_line_with_output() -> None:
    line = format_tool_token_line(100, 25)
    assert line == "tool tokens (compact JSON): input=100, output=25, saved=75 (75.0%)"


def test_format_tool_token_arrow_line() -> None:
    line = format_tool_token_arrow_line(100, 25, tokens_saved=75)
    assert line == "tool tokens (compact JSON): 100 -> 25 (saved 75, 75.0%)"


def test_format_agent_tools_token_lines_with_output() -> None:
    lines = format_agent_tools_token_lines(100, 25)
    assert lines == [
        "\nTools before cleaning (tokens): 100",
        "Tools after cleaning agent-tools (compact JSON): tokens=25",
    ]


def test_format_agent_tools_token_lines_with_tool_count() -> None:
    lines = format_agent_tools_token_lines(56590, tool_count=202)
    assert lines == [
        "\nTools before cleaning (compact JSON): tools=202, tokens=56590",
    ]


def test_build_preview_token_stats_net_savings() -> None:
    stats = build_preview_token_stats(
        tokens_in=1000,
        tokens_out=200,
        frontend_tool_count=3,
        frontend_tokens=50,
    )
    assert stats["tokens_saved"] == 800
    assert stats["net_tokens_out"] == 250
    assert stats["net_tokens_saved"] == 750


def test_format_preview_token_summary_lines_order() -> None:
    stats = build_preview_token_stats(
        tokens_in=1000,
        tokens_out=200,
        tool_count_in=202,
        frontend_tool_count=3,
        frontend_tokens=50,
    )
    lines = format_preview_token_summary_lines(stats)
    assert lines[0] == "\nTools before cleaning (compact JSON): tools=202, tokens=1000"
    assert lines[1] == "Tools after cleaning agent-tools (compact JSON): tokens=200"
    assert lines[2].startswith("Frontend stubs (compact JSON):")
    assert lines[3] == "Saved (tokens): 750 (75.0%) = 1000-200+50"
