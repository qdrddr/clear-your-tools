"""MCPC-specific tool catalog entries and prune result splitting."""

from __future__ import annotations

from typing import Any

from cyt.indexer.build import anthropic_tools_to_catalog_entries

MCPC_SERVER_INSTRUCTIONS_SUFFIX = "/__server_instructions__"
MCPC_SERVER_INSTRUCTIONS_KIND = "mcpc_server_instructions"


def _session_instruction_text(tool: dict[str, Any]) -> str:
    return str(tool.get("server_instructions") or "").strip()


def mcpc_tools_to_catalog_entries(
    tools: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[Any]]:
    """Convert MCPC tools to catalog entries, adding synthetic instruction chunks."""
    entries, enums = anthropic_tools_to_catalog_entries(tools)
    seen_sessions: set[str] = set()
    for tool in tools:
        session = str(tool.get("mcpc_session") or "").strip()
        if not session or session in seen_sessions:
            continue
        instructions = _session_instruction_text(tool)
        if not instructions:
            continue
        seen_sessions.add(session)
        entry_id = f"{session}{MCPC_SERVER_INSTRUCTIONS_SUFFIX}"
        entries.append(
            {
                "id": entry_id,
                "name": entry_id,
                "description": instructions,
                "input_schema": {"type": "object", "properties": {}},
                "cyt_chunk_kind": MCPC_SERVER_INSTRUCTIONS_KIND,
            },
        )
    return entries, enums


def split_mcpc_prune_result(
    pruned_tools: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], set[str]]:
    """Split real tools from synthetic server-instruction survivors."""
    real_tools: list[dict[str, Any]] = []
    surviving_instruction_sessions: set[str] = set()
    suffix = MCPC_SERVER_INSTRUCTIONS_SUFFIX
    for tool in pruned_tools:
        name = str(tool.get("name") or "")
        if name.endswith(suffix):
            session = name[: -len(suffix)]
            if session:
                surviving_instruction_sessions.add(session)
            continue
        real_tools.append(tool)
    return real_tools, surviving_instruction_sessions
