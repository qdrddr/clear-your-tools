"""Tests for tier feedback tool-name canonicalization."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from cyt.tiers.adapters.tools import resolve_canonical_tool_name_for_tiers, tool_entity_id
from cyt.tiers.feedback import record_tool_used_feedback
from cyt.tiers.manager import TierManager
from cyt.tiers.models import Tier
from cyt_client.tool_gate import (
    _extract_tool_call,
    extract_gated_tool_use_feedback,
    normalize_mcp_tool_name,
)


def test_resolve_canonical_tool_name_from_bare_tool_name_field() -> None:
    master = [
        {
            "name": "codebase-memory_search_graph",
            "tool_name": "search_graph",
            "server_key": "codebase-memory",
        },
        {
            "name": "jcodemunch_search_symbols",
            "tool_name": "search_symbols",
            "server_key": "jcodemunch",
        },
    ]
    config: dict = {}
    with patch("cyt.tools.master_catalog.get_master_tool_catalog", return_value=master):
        assert (
            resolve_canonical_tool_name_for_tiers(
                "search_graph",
                config=config,
                catalog="cyt_mcp",
            )
            == "codebase-memory_search_graph"
        )
        assert (
            resolve_canonical_tool_name_for_tiers(
                "jcodemunch_search_symbols",
                config=config,
                catalog="cyt_mcp",
            )
            == "jcodemunch_search_symbols"
        )


def test_resolve_canonical_tool_name_from_unique_suffix() -> None:
    master = [{"name": "semble_search"}, {"name": "gitnexus_query"}]
    config: dict = {}
    with patch("cyt.tools.master_catalog.get_master_tool_catalog", return_value=master):
        assert (
            resolve_canonical_tool_name_for_tiers("search", config=config, catalog="cyt_mcp")
            == "semble_search"
        )
        assert (
            resolve_canonical_tool_name_for_tiers("query", config=config, catalog="cyt_mcp")
            == "gitnexus_query"
        )


def test_extract_gated_tool_use_feedback_returns_wire_name(tmp_path: Path) -> None:
    log_path = tmp_path / "session.log"
    log_path.write_text(
        json.dumps(
            {"kind": "session_state", "key": "session_state:inject", "tools_inject_enabled": True},
        )
        + "\n"
        + json.dumps(
            {
                "kind": "tool_catalog",
                "key": "tool_catalog:cyt_mcp",
                "catalog": "cyt_mcp",
                "hash": "h",
                "tools": [
                    {
                        "name": "jcodemunch_search_symbols",
                        "server_key": "jcodemunch",
                        "tool_name": "search_symbols",
                        "input_schema": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}, "repo": {"type": "string"}},
                            "required": ["query", "repo"],
                        },
                    },
                ],
            },
        )
        + "\n",
        encoding="utf-8",
    )
    payload = {
        "tool_name": "jcodemunch_search_symbols",
        "tool_input": {"query": "bm25", "repo": "demo"},
        "workspace_roots": [str(tmp_path)],
    }
    with patch("cyt_client.tool_gate.session_log_path", return_value=log_path):
        feedback = extract_gated_tool_use_feedback(payload)
    assert feedback is not None
    assert feedback["tool_name"] == "jcodemunch_search_symbols"


def test_normalize_user_cyt_mcp_dynamic_tool_name() -> None:
    assert (
        normalize_mcp_tool_name("user-cyt-mcp-codebase-memory_search_graph", agent="cursor")
        == "codebase-memory_search_graph"
    )


def test_extract_call_dynamic_tool_namespace(tmp_path: Path) -> None:
    payload = {
        "tool_name": "CallDynamicTool",
        "tool_input": {
            "namespace": "user-cyt-mcp",
            "toolName": "gitnexus_query",
            "arguments": {"search_query": "bm25"},
        },
    }
    tool_name, args = _extract_tool_call(payload)
    assert tool_name == "gitnexus_query"
    assert isinstance(args, dict)
    assert args.get("search_query") == "bm25"


def test_record_tool_used_feedback_promotes_master_catalog_entity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".git").mkdir()
    db = str(tmp_path / "tier_state.db")
    manager = TierManager(tmp_path, db)

    master = [
        {
            "name": "jcodemunch_search_symbols",
            "cyt_catalog_source": "cyt_mcp",
            "input_schema": {"type": "object"},
        },
    ]
    config = {
        "tools": {"hook_sources": ["cyt_mcp"], "tiers": {"mode": "live"}},
        "hook": {"workspace": str(tmp_path)},
    }
    monkeypatch.setattr(
        "cyt.tools.master_catalog.get_master_tool_catalog",
        lambda _cfg, blocking=False: master,
    )
    monkeypatch.setattr(
        "cyt.hook.workspace_config.hook_workspace_from_config",
        lambda _cfg: tmp_path,
    )
    monkeypatch.setattr(
        "cyt.tiers.config.tiers_active",
        lambda _cfg, kind="tool": kind == "tool",
    )
    monkeypatch.setattr(
        "cyt.tiers.manager.get_tier_manager",
        lambda _cfg, workspace=None: manager,
    )
    monkeypatch.setattr(
        "cyt.tiers.adapters.tools.configured_tool_catalog_sources",
        lambda _cfg: frozenset({"cyt_mcp"}),
    )
    monkeypatch.setattr(
        "cyt.tiers.adapters.tools.tool_tracked_for_config",
        lambda _tool, _cfg: True,
    )

    record_tool_used_feedback(
        tool_name="search_symbols",
        catalog="cyt_mcp",
        config=config,
        workspace=tmp_path,
        optional_used=True,
    )

    entity_id = tool_entity_id(
        {"name": "jcodemunch_search_symbols", "cyt_catalog_source": "cyt_mcp"},
    )
    state = manager._states.get(("tool", entity_id))
    assert state is not None
    assert state.effective_tier == Tier.HOT
    assert state.temp_promotion_until_ms is not None
