"""Tests for cyt-mcp tier feedback push client."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from cyt_mcp.config import sample_aggregator_config
from cyt_mcp.tier_feedback_push import _build_payload, schedule_tool_use_feedback


def test_build_payload_skips_without_workspace_root() -> None:
    config = sample_aggregator_config(catalog_scope="user", workspace_root=None)
    payload = _build_payload(
        config=config,
        tool_name="codebase-memory_search_graph",
        args={"query": "auth"},
        success=True,
        catalog_content_hash="abc",
        mcp_server="codebase-memory",
        bare_tool_name="search_graph",
        input_schema={"type": "object"},
    )
    assert payload is None


def test_build_payload_includes_success_and_schema(tmp_path: Path) -> None:
    config = sample_aggregator_config(
        catalog_scope="workspace",
        workspace_root=tmp_path,
    )
    payload = _build_payload(
        config=config,
        tool_name="codebase-memory_search_graph",
        args={"query": "auth"},
        success=True,
        catalog_content_hash="abc",
        mcp_server="codebase-memory",
        bare_tool_name="search_graph",
        input_schema={"type": "object"},
    )
    assert payload is not None
    assert payload["success"] is True
    assert payload["source"] == "cyt_mcp"
    assert payload["catalog_content_hash"] == "abc"
    assert payload["mcp_server"] == "codebase-memory"
    assert payload["input_schema"] == {"type": "object"}


def test_schedule_tool_use_feedback_posts_in_background(tmp_path: Path) -> None:
    config = sample_aggregator_config(
        catalog_scope="workspace",
        workspace_root=tmp_path,
    )
    posted: list[dict] = []

    def fake_push(payload: dict) -> None:
        posted.append(payload)

    with (
        patch(
            "cyt_mcp.tier_feedback_push.resolve_hook_path",
            return_value="http://127.0.0.1:8834/hook/tier/feedback",
        ),
        patch("cyt_mcp.tier_feedback_push._push_sync", side_effect=fake_push),
    ):
        schedule_tool_use_feedback(
            config=config,
            tool_name="jcodemunch_search_symbols",
            args={"query": "foo"},
            success=False,
        )
    assert len(posted) == 1
    assert posted[0]["success"] is False
    assert posted[0]["tool_name"] == "jcodemunch_search_symbols"
