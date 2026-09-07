"""MCP wire stub catalog — retain vocabulary for tools/list projection."""

from __future__ import annotations

import copy
from typing import Any, TypedDict, cast

DEFAULT_STUB_NAME = "basic"

DEFAULT_STUB_BY_AGENT: dict[str, str] = {
    "codex": "codex",
    "cursor": "basic",
    "claude": "basic",
}


class RetainSpec(TypedDict, total=False):
    tool: list[str]
    required_properties: list[str]
    optional_properties: list[str]


class StubDef(TypedDict, total=False):
    name: str
    description: str
    always: RetainSpec


DEFAULT_STUBS: list[dict[str, Any]] = [
    {
        "name": "basic",
        "description": "Tool name only — smallest stable wire surface.",
        "always": {
            "tool": ["name"],
            "required_properties": [],
            "optional_properties": [],
        },
    },
    {
        "name": "tool_root_required",
        "description": "Tool name and required property names (no property descriptions).",
        "always": {
            "tool": ["name"],
            "required_properties": ["name"],
            "optional_properties": [],
        },
    },
    {
        "name": "codex",
        "description": "Tool name and tool description (OpenAI Responses API expects description on wire).",
        "always": {
            "tool": ["name", "description"],
            "required_properties": [],
            "optional_properties": [],
        },
    },
    {
        "name": "codex_tool_root_required",
        "description": "Codex wire shape plus required property names (no property descriptions).",
        "always": {
            "tool": ["name", "description"],
            "required_properties": ["name"],
            "optional_properties": [],
        },
    },
]


def _stub_entry_name(item: object) -> str | None:
    if not isinstance(item, dict):
        return None
    name = item.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def merge_stubs_by_name(base: list[Any], overlay: list[Any]) -> list[dict[str, Any]]:
    by_name: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    def ingest(items: list[Any]) -> None:
        for item in items:
            name = _stub_entry_name(item)
            if name is None:
                continue
            if name not in by_name:
                order.append(name)
            by_name[name] = copy.deepcopy(cast(dict[str, Any], item))

    ingest(base)
    ingest(overlay)
    return [by_name[name] for name in order]


def _tools_block(raw: dict[str, Any]) -> dict[str, Any]:
    pruning = raw.get("pruning")
    if not isinstance(pruning, dict):
        return {}
    tools = pruning.get("tools")
    return tools if isinstance(tools, dict) else {}


def resolved_stubs(raw: dict[str, Any]) -> dict[str, StubDef]:
    tools = _tools_block(raw)
    stub_list = tools.get("stubs")
    items = stub_list if isinstance(stub_list, list) and stub_list else DEFAULT_STUBS
    out: dict[str, StubDef] = {}
    for item in merge_stubs_by_name(DEFAULT_STUBS, items):
        name = _stub_entry_name(item)
        if name is not None:
            out[name] = cast(StubDef, item)
    return out


def default_stub_name(raw: dict[str, Any]) -> str:
    tools = _tools_block(raw)
    stub = tools.get("stub")
    if isinstance(stub, str) and stub.strip():
        return stub.strip()
    return DEFAULT_STUB_NAME


def stub_by_agent_map(raw: dict[str, Any]) -> dict[str, str]:
    tools = _tools_block(raw)
    mapping = tools.get("stub_by_agent")
    merged = dict(DEFAULT_STUB_BY_AGENT)
    if isinstance(mapping, dict):
        for agent, stub in mapping.items():
            if isinstance(agent, str) and isinstance(stub, str) and agent.strip() and stub.strip():
                merged[agent.strip()] = stub.strip()
    return merged


def resolve_stub_name(raw: dict[str, Any], agent: str) -> str:
    agent_key = agent.strip() or "cursor"
    by_agent = stub_by_agent_map(raw)
    if agent_key in by_agent:
        return by_agent[agent_key]
    return default_stub_name(raw)


def resolve_stub_retain(raw: dict[str, Any], agent: str) -> RetainSpec:
    name = resolve_stub_name(raw, agent)
    stub = resolved_stubs(raw).get(name)
    if stub is None:
        stub = resolved_stubs({}).get(DEFAULT_STUB_NAME, cast(StubDef, DEFAULT_STUBS[0]))
    always = stub.get("always")
    if isinstance(always, dict):
        return always
    return cast(RetainSpec, DEFAULT_STUBS[0]["always"])


def retain_includes_tool_field(retain: RetainSpec, field: str) -> bool:
    tool_fields = retain.get("tool")
    if not isinstance(tool_fields, list):
        return field == "name"
    return field in tool_fields


def retain_includes_required_names(retain: RetainSpec) -> bool:
    req = retain.get("required_properties")
    return isinstance(req, list) and "name" in req
