"""Tests for multi-source hook tool injection formatting."""

from __future__ import annotations

from unittest.mock import patch

from cyt.tools.hook import gate_and_format_hook_tools
from cyt.tools.source_inject import (
    format_definitions_source_section,
    format_executor_source_section,
    format_mcp_source_section,
    format_multi_source_agent_tools,
)


def test_format_multi_source_agent_tools_omits_empty_sources() -> None:
    block = format_multi_source_agent_tools(
        {
            "mcpc": format_mcp_source_section(
                [
                    {
                        "name": "@s/t",
                        "tool_name": "t",
                        "mcpc_session": "@s",
                        "description": "d",
                        "input_schema": {"type": "object"},
                        "server_name": "S",
                    },
                ],
            ),
        },
    )
    assert "<agent-tools" in block
    assert "<mcp>" in block
    assert "\n<executor>" not in block
    assert "\n<definitions>" not in block


def test_format_executor_source_section_wraps_tools() -> None:
    section = format_executor_source_section(
        [{"name": "alpha", "description": "A", "input_schema": {"type": "object"}}],
    )
    assert "executor" in section.lower() or "<executor>" in section
    assert "<tool " in section
    assert section.endswith("</executor>")


def test_format_definitions_source_section_wraps_tools() -> None:
    section = format_definitions_source_section(
        [{"name": "beta", "description": "B", "input_schema": {"type": "object"}}],
    )
    assert "<definitions>" in section
    assert "beta" in section


def test_gate_and_format_single_source_section_not_legacy_mcpc() -> None:
    """Executor-only pruned tools must not fall back to MCPC formatting."""
    pruned = [
        {
            "name": "Shell",
            "description": "Run shell",
            "input_schema": {"type": "object"},
            "cyt_catalog_source": "executor",
        },
    ]
    config = {"tools": {"hook": {"sources": ["mcpc", "executor"]}}}
    payload: dict = {}
    with patch("cyt.tools.hook.uses_mcpc_tool_catalog", return_value=True):
        formatted, _logs = gate_and_format_hook_tools(
            pruned,
            config=config,
            payload=payload,
            session_text="",
        )
    assert "<executor>" in formatted
    assert "Shell" in formatted
    assert "\n<mcp>" not in formatted
