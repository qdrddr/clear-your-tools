"""Tests for backend schema profile helpers and injection hint matrix."""

from __future__ import annotations

import pytest

from cyt.pruners.tools_filter import merge_api_tool_onto_original
from cyt.tiers.adapters.tools import merge_t4_tools
from cyt.tools.inject import format_tool_item
from cyt.tools.injection_schema import (
    _validate_t2_required_schema,
    backend_schema_from_tool,
    backend_schema_has_only_optionals,
    backend_schema_has_required,
    backend_schema_is_empty,
    ensure_tool_injection_schema,
    injection_needs_definitions_lookup,
)
from cyt.tiers.tool_token_materialization import CYT_BACKEND_INPUT_SCHEMA, stamp_tool_dual_schema


def _tool(
    *,
    tier: str,
    injected: dict,
    backend: dict,
) -> dict:
    return {
        "name": "demo_tool",
        "cyt_injection_tier": tier,
        "input_schema": injected,
        "cyt_backend_input_schema": backend,
    }


def test_backend_schema_profile_helpers() -> None:
    empty = {"type": "object", "properties": {}}
    required_only = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }
    required_optional = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "required": ["query"],
    }
    optional_only = {
        "type": "object",
        "properties": {"verbose": {"type": "boolean"}},
    }

    assert backend_schema_is_empty(empty)
    assert backend_schema_has_required(required_only)
    assert backend_schema_has_required(required_optional)
    assert not backend_schema_has_only_optionals(required_optional)
    assert backend_schema_has_only_optionals(optional_only)


def test_injection_needs_definitions_lookup_t2_matrix() -> None:
    empty = {"type": "object", "properties": {}}
    required = {
        "type": "object",
        "properties": {"confirm": {"type": "boolean"}},
        "required": ["confirm"],
    }
    optional_only = {
        "type": "object",
        "properties": {"verbose": {"type": "boolean"}},
    }

    assert not injection_needs_definitions_lookup(_tool(tier="t2", injected=empty, backend=empty))
    assert not injection_needs_definitions_lookup(
        _tool(tier="t2", injected={"properties": {"confirm": {}}}, backend=required),
    )
    assert injection_needs_definitions_lookup(
        _tool(tier="t2", injected=empty, backend=optional_only),
    )


def test_injection_needs_definitions_lookup_t3_matrix() -> None:
    empty = {"type": "object", "properties": {}}
    required_only = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }
    required_optional = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "required": ["query"],
    }
    optional_only = {
        "type": "object",
        "properties": {"repo": {"type": "string"}},
    }

    assert not injection_needs_definitions_lookup(_tool(tier="t3", injected=empty, backend=empty))
    assert not injection_needs_definitions_lookup(
        _tool(tier="t3", injected=empty, backend=required_only),
    )
    assert not injection_needs_definitions_lookup(
        _tool(
            tier="t3",
            injected={"type": "object", "properties": {"query": {"type": "string"}}},
            backend=required_optional,
        ),
    )
    assert not injection_needs_definitions_lookup(
        _tool(
            tier="t3",
            injected={"type": "object", "properties": {"query": {"type": "string"}}},
            backend=required_optional,
        ),
    )
    assert injection_needs_definitions_lookup(
        _tool(tier="t3", injected=empty, backend=optional_only),
    )
    assert not injection_needs_definitions_lookup(
        _tool(
            tier="t3",
            injected={"type": "object", "properties": {"repo": {"type": "string"}}},
            backend=optional_only,
        ),
    )


def test_backend_schema_from_tool_prefers_stamped_key() -> None:
    tool = {
        "input_schema": {"properties": {"a": {"type": "string"}}},
        "cyt_backend_input_schema": {"properties": {"b": {"type": "string"}}},
    }
    assert backend_schema_from_tool(tool) == tool["cyt_backend_input_schema"]


def test_ensure_tool_injection_schema_restores_t3_required_from_backend() -> None:
    tool = {
        "name": "gitnexus_query",
        "cyt_injection_tier": "t3",
        "input_schema": {"type": "object", "properties": {}},
        "cyt_backend_input_schema": {
            "type": "object",
            "properties": {
                "search_query": {"type": "string"},
                "repo": {"type": "string"},
            },
            "required": ["search_query"],
        },
    }
    merged = ensure_tool_injection_schema(tool)
    props = merged["input_schema"]["properties"]
    assert "search_query" in props
    assert "repo" not in props


def test_ensure_tool_injection_schema_restores_t2_required_from_backend() -> None:
    tool = {
        "name": "context-mode_ctx_purge",
        "cyt_injection_tier": "t2",
        "input_schema": {"type": "object", "properties": {}},
        "cyt_backend_input_schema": {
            "type": "object",
            "properties": {
                "confirm": {"type": "boolean"},
                "scope": {"type": "string"},
            },
            "required": ["confirm"],
        },
    }
    merged = ensure_tool_injection_schema(tool)
    props = merged["input_schema"]["properties"]
    assert "confirm" in props
    assert "scope" not in props


def test_validate_t2_required_schema_raises_when_required_missing() -> None:
    tool = {"name": "broken_tool", "cyt_injection_tier": "t2"}
    backend = {
        "type": "object",
        "properties": {"confirm": {"type": "boolean"}},
        "required": ["confirm"],
    }
    schema = {"type": "object", "properties": {}}
    with pytest.raises(ValueError, match="missing required schema properties"):
        _validate_t2_required_schema(tool, schema, backend)


def test_merge_t4_tools_preserves_backend_schema() -> None:
    pruned = [
        stamp_tool_dual_schema(
            {
                "name": "pruned_tool",
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
            "T3",
        ),
    ]
    t4 = stamp_tool_dual_schema(
        {
            "name": "t4_tool",
            "input_schema": {"type": "object", "properties": {}},
        },
        "T4",
    )
    merged = merge_t4_tools(pruned, [t4])
    by_name = {tool["name"]: tool for tool in merged}
    assert by_name["t4_tool"][CYT_BACKEND_INPUT_SCHEMA] == t4[CYT_BACKEND_INPUT_SCHEMA]
    assert by_name["pruned_tool"][CYT_BACKEND_INPUT_SCHEMA] == pruned[0][CYT_BACKEND_INPUT_SCHEMA]


def test_format_tool_item_never_emits_backend_schema_key() -> None:
    tool = stamp_tool_dual_schema(
        {
            "name": "demo_tool",
            "description": "Demo",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            "cyt_injection_tier": "t3",
        },
        "T3",
    )
    tool["cyt_injection_tier"] = "t3"
    item = format_tool_item(tool)
    assert "cyt_backend_input_schema" not in item
    assert "'input_schema':" in item


def test_format_tool_item_t3_required_optional_pruned_to_required_only() -> None:
    backend = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "required": ["query"],
    }
    tool = ensure_tool_injection_schema(
        {
            "name": "demo_tool",
            "description": "Demo",
            "cyt_injection_tier": "t3",
            "input_schema": {"type": "object", "properties": {}},
            "cyt_backend_input_schema": backend,
        },
    )
    item = format_tool_item(tool)
    assert "'query'" in item
    assert "'limit'" not in item
    assert "get-tool-definitions" not in item


def test_format_tool_item_t3_required_optional_keeps_surviving_optionals() -> None:
    tool = {
        "name": "demo_tool",
        "description": "Demo",
        "cyt_injection_tier": "t3",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
        },
        "cyt_backend_input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
        },
    }
    item = format_tool_item(tool)
    assert "'query'" in item
    assert "'limit'" in item
    assert "get-tool-definitions" not in item


def test_format_tool_item_t3_optional_only_partial_survivors() -> None:
    tool = {
        "name": "demo_tool",
        "description": "Demo",
        "cyt_injection_tier": "t3",
        "input_schema": {
            "type": "object",
            "properties": {"repo": {"type": "string"}},
        },
        "cyt_backend_input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "symbol": {"type": "string"},
            },
        },
    }
    item = format_tool_item(tool)
    assert "'repo'" in item
    assert "'symbol'" not in item
    assert "get-tool-definitions" not in item


def test_merge_api_tool_onto_original_preserves_backend_schema() -> None:
    original = stamp_tool_dual_schema(
        {
            "name": "demo_tool",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
        "T2",
    )
    merged = merge_api_tool_onto_original(
        original,
        {
            "name": "demo_tool",
            "description": "Updated",
            "input_schema": {"type": "object", "properties": {}},
        },
    )
    assert merged[CYT_BACKEND_INPUT_SCHEMA] == original[CYT_BACKEND_INPUT_SCHEMA]
    assert merged["description"] == "Updated"
