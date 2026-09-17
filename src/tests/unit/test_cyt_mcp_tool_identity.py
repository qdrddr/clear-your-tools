"""Unit tests for cyt-mcp wire name ↔ backend identity mapping."""

from __future__ import annotations

from cyt_mcp.tool_identity import (
    CytMcpToolIdentity,
    canonical_backend_identity,
    enrich_tool_identity,
    is_canonical_schema_identity,
    resolve_backend_identity,
    server_keys_for_enrichment,
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


def test_resolve_backend_identity_infers_bare_name_when_session_log_omits_tool_name() -> None:
    assert resolve_backend_identity(
        {"name": "grep", "server_key": "fff"},
    ) == ("fff", "grep")
    assert resolve_backend_identity(
        {"name": "gitnexus_query", "server_key": "gitnexus"},
    ) == ("gitnexus", "query")


def test_canonical_backend_identity_prefers_wire_split_over_wrong_explicit_fields() -> None:
    server_keys = ["codebase-memory", "semble", "search"]
    server, bare = canonical_backend_identity(
        {
            "name": "codebase-memory_search_graph",
            "server_key": "search",
            "tool_name": "graph",
        },
        server_keys,
    )
    assert (server, bare) == ("codebase-memory", "search_graph")


def test_is_canonical_schema_identity_rejects_partial_split() -> None:
    server_keys = ["codebase-memory", "semble", "fff"]
    assert not is_canonical_schema_identity("search", "graph", server_keys)
    assert is_canonical_schema_identity("codebase-memory", "search_graph", server_keys)


def test_server_keys_for_enrichment_derives_prefix_from_wire_names() -> None:
    tools = [{"name": "hedl_batch"}, {"name": "context-mode_ctx_search"}]
    keys = server_keys_for_enrichment(tools)
    assert "hedl" in keys
    assert "context-mode" in keys


def test_enrich_hedl_batch_without_configured_server_keys() -> None:
    tool = {
        "name": "hedl_batch",
        "description": "HEDL batch tool (single-underscore catalog name; backend tool is batch)",
        "input_schema": {"type": "object", "properties": {}},
    }
    enriched = enrich_tool_identity(tool, server_keys_for_enrichment([tool]))
    assert enriched["server_key"] == "hedl"
    assert enriched["tool_name"] == "batch"
    assert enriched["name"] == "hedl_batch"
