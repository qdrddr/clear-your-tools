"""Unit tests for cyt-mcp wire name ↔ backend identity mapping."""

from __future__ import annotations

from cyt_mcp.tool_identity import (
    CytMcpToolIdentity,
    enrich_tool_identity,
    resolve_backend_identity,
    split_wire_name,
    wire_name_for,
)


def test_wire_name_for() -> None:
    assert wire_name_for("graphify", "query_graph") == "graphify_query_graph"


def test_split_wire_name_longest_prefix_wins() -> None:
    server_keys = ["code", "codebase-memory", "codebase-memory-mcp"]
    identity = split_wire_name("codebase-memory-mcp_search_graph", server_keys)
    assert identity == CytMcpToolIdentity(
        wire_name="codebase-memory-mcp_search_graph",
        server_key="codebase-memory-mcp",
        backend_tool_name="search_graph",
    )


def test_split_wire_name_hyphenated_server() -> None:
    identity = split_wire_name(
        "codebase-memory_search_graph",
        ["codebase-memory", "graphify"],
    )
    assert identity == CytMcpToolIdentity(
        wire_name="codebase-memory_search_graph",
        server_key="codebase-memory",
        backend_tool_name="search_graph",
    )


def test_split_wire_name_unknown_returns_none() -> None:
    assert split_wire_name("demo_search", []) is None
    assert split_wire_name("demo_search", ["other"]) is None


def test_enrich_tool_identity_preserves_explicit_fields() -> None:
    tool = {
        "name": "legacy_name",
        "server_key": "graphify",
        "tool_name": "query_graph",
    }
    enriched = enrich_tool_identity(tool, ["graphify"])
    assert enriched["name"] == "graphify_query_graph"
    assert enriched["server_key"] == "graphify"
    assert enriched["tool_name"] == "query_graph"


def test_enrich_tool_identity_splits_wire_name_at_catalog_build() -> None:
    tool = {"name": "graphify_query_graph", "input_schema": {"type": "object"}}
    enriched = enrich_tool_identity(tool, ["graphify", "codebase-memory"])
    assert enriched["server_key"] == "graphify"
    assert enriched["tool_name"] == "query_graph"


def test_resolve_backend_identity_requires_explicit_fields() -> None:
    assert resolve_backend_identity(
        {
            "name": "graphify_query_graph",
            "server_key": "graphify",
            "tool_name": "query_graph",
        },
    ) == ("graphify", "query_graph")
    assert resolve_backend_identity({"name": "graphify_query_graph"}) == (
        "unknown",
        "graphify_query_graph",
    )
