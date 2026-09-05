"""Tests for MCPC granular pre-exposure."""

from __future__ import annotations

from typing import Any

from cyt.injection.mcpc_pre_exposed import (
    compute_mcpc_pre_exposure_flags,
    filter_pre_exposed_mcpc_tools,
)
from cyt.tools.inject import _xml_single_quoted_attr
from cyt.tools.mcpc_inject import format_mcpc_agent_tools


def _tool(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "@ctx7/resolve-library-id",
        "tool_name": "resolve-library-id",
        "mcpc_session": "@ctx7",
        "title": "Resolve Context7 Library ID",
        "description": "Resolve a library id",
        "input_schema": {
            "type": "object",
            "properties": {"libraryName": {"type": "string"}},
        },
        "server_name": "Context7",
        "server_instructions": "Use this server for docs.",
        "server_description": "Context7 documentation server.",
    }
    if overrides:
        base.update(overrides)
    return base


def test_filter_pre_exposed_mcpc_tools_drops_verbatim_fragment() -> None:
    kept = _tool({"description": "Fresh tool"})
    dropped = _tool({"description": "Already seen"})
    from cyt.tools.mcpc_inject import _format_mcpc_tool_item

    session_text = _format_mcpc_tool_item(dropped)
    filtered = filter_pre_exposed_mcpc_tools([dropped, kept], session_text)
    assert len(filtered) == 1
    assert filtered[0]["description"] == "Fresh tool"


def test_format_mcpc_agent_tools_omits_pre_exposed_tool_description() -> None:
    tool = _tool()
    description = tool["description"]
    session_text = (
        f"<tool name='resolve-library-id' description='{_xml_single_quoted_attr(description)}'>"
    )
    text = format_mcpc_agent_tools([tool], session_text=session_text)
    assert "name='resolve-library-id'" in text
    assert "description='Resolve a library id'" not in text


def test_format_mcpc_agent_tools_omits_pre_exposed_server_instructions() -> None:
    tool = _tool()
    instructions = tool["server_instructions"]
    session_text = (
        f"<server name='Context7' instructions='{_xml_single_quoted_attr(instructions)}'>"
    )
    text = format_mcpc_agent_tools(
        [tool],
        session_text=session_text,
        surviving_instruction_sessions={"@ctx7"},
    )
    assert "<server name='Context7'>" in text or "<server name='Context7' " in text
    assert "instructions='Use this server for docs.'" not in text


def test_format_mcpc_agent_tools_includes_server_description() -> None:
    tool = _tool()
    text = format_mcpc_agent_tools([tool])
    assert "description='Context7 documentation server.'" in text


def test_compute_mcpc_pre_exposure_flags_detects_agent_tools_intro() -> None:
    from cyt.tools.mcpc_inject import _mcpc_agent_tools_description

    intro = _mcpc_agent_tools_description()
    session_text = f"<agent-tools>\n{intro}\n</agent-tools>"
    flags = compute_mcpc_pre_exposure_flags([_tool()], session_text)
    assert flags.omit_agent_tools_description is True
