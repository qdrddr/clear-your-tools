"""Tests for MCPC instruction pseudo-entry pruning."""

from __future__ import annotations

from cyt.tools.mcpc_prune import (
    MCPC_SERVER_INSTRUCTIONS_SUFFIX,
    mcpc_tools_to_catalog_entries,
    split_mcpc_prune_result,
)


def _tool(session: str = "@ctx7") -> dict:
    return {
        "name": f"{session}/resolve-library-id",
        "tool_name": "resolve-library-id",
        "mcpc_session": session,
        "description": "Resolve library id",
        "input_schema": {"type": "object", "properties": {}},
        "server_name": "Context7",
        "server_instructions": "Use this server for docs.",
    }


def test_mcpc_tools_to_catalog_entries_adds_instruction_pseudo_entry() -> None:
    entries, _enums = mcpc_tools_to_catalog_entries([_tool()])
    ids = [entry.get("id") or entry.get("name") for entry in entries]
    assert f"@ctx7{MCPC_SERVER_INSTRUCTIONS_SUFFIX}" in ids


def test_split_mcpc_prune_result_separates_pseudo_entries() -> None:
    pruned = [
        _tool(),
        {
            "name": f"@ctx7{MCPC_SERVER_INSTRUCTIONS_SUFFIX}",
            "description": "Use this server for docs.",
        },
    ]
    real_tools, surviving = split_mcpc_prune_result(pruned)
    assert len(real_tools) == 1
    assert surviving == {"@ctx7"}


def test_split_mcpc_prune_result_empty_when_no_pseudo_entries() -> None:
    real_tools, surviving = split_mcpc_prune_result([_tool()])
    assert len(real_tools) == 1
    assert surviving == set()
