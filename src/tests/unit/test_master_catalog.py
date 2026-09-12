"""Tests for multi-source master tool catalog."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from unittest.mock import patch

import pytest

from cyt.config import load_config
from cyt.tiers.tool_token_materialization import clear_carried_token_memo
from cyt.tools.master_catalog import (
    _cache_key_for_config,
    _get_state,
    _snapshot_master_tools,
    build_master_tools,
    clear_master_catalog_cache,
    get_master_tool_catalog,
    rebuild_master_catalog,
)


@pytest.fixture(autouse=True)
def _reset_master_cache() -> Iterator[None]:
    clear_master_catalog_cache()
    yield
    clear_master_catalog_cache()


def test_build_master_tools_stamps_cyt_catalog_source() -> None:
    clear_carried_token_memo()
    merged = build_master_tools(
        [
            ("mcpc", [{"name": "a/one"}]),
            ("executor", [{"name": "tool_one"}]),
        ],
    )
    assert len(merged) == 2
    assert merged[0]["cyt_catalog_source"] == "mcpc"
    assert merged[1]["cyt_catalog_source"] == "executor"


def test_build_master_tools_stamps_token_count() -> None:
    clear_carried_token_memo()
    merged = build_master_tools(
        [
            (
                "cyt_mcp",
                [
                    {
                        "name": "search",
                        "description": "Search the codebase.",
                        "input_schema": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"],
                        },
                    },
                ],
            ),
        ],
    )
    assert len(merged) == 1
    assert isinstance(merged[0].get("token_count"), int)
    assert merged[0]["token_count"] > 0


def test_get_master_tool_catalog_concatenates_configured_sources() -> None:
    config = load_config()
    config["pruning"]["inject_via"] = {"cursor": "hook", "claude": "hook", "codex": "hook"}
    config["tools"]["enabled"] = True
    config["tools"]["hook"]["tools_from"] = ["mcpc", "executor"]

    with (
        patch(
            "cyt.tools.master_catalog._load_source_tools",
            side_effect=lambda _cfg, source, **_: [{"name": f"{source}-tool"}],
        ),
        patch("cyt.tools.catalog_cache.schedule_decomposed_catalog_refresh_for_sources"),
    ):
        catalog = get_master_tool_catalog(config, blocking=True)

    assert catalog is not None
    assert len(catalog) == 2
    sources = {tool["cyt_catalog_source"] for tool in catalog}
    assert sources == {"mcpc", "executor"}
    assert all(isinstance(tool.get("token_count"), int) for tool in catalog)


def test_get_master_tool_catalog_returns_empty_list_not_none_on_cold_start() -> None:
    config = load_config()
    config["pruning"]["inject_via"] = {"cursor": "hook", "claude": "hook", "codex": "hook"}
    config["tools"]["enabled"] = True
    config["tools"]["hook"]["tools_from"] = ["mcpc"]

    with patch("cyt.tools.master_catalog._load_source_tools", return_value=[]):
        catalog = get_master_tool_catalog(config, blocking=False)

    assert catalog == []


def test_rebuild_master_catalog_preserves_prior_source_on_empty_non_blocking_read() -> None:
    config = load_config()
    config["pruning"]["inject_via"] = {"cursor": "hook", "claude": "hook", "codex": "hook"}
    config["tools"]["enabled"] = True
    config["tools"]["hook"]["tools_from"] = ["mcpc", "executor"]

    with (
        patch(
            "cyt.tools.master_catalog._load_source_tools",
            side_effect=lambda _cfg, source, **_: [{"name": f"{source}-tool"}],
        ),
        patch("cyt.tools.catalog_cache.schedule_decomposed_catalog_refresh_for_sources"),
    ):
        rebuild_master_catalog(config, blocking=True)

    with (
        patch(
            "cyt.tools.master_catalog._load_source_tools",
            side_effect=lambda _cfg, source, **_: (
                [{"name": "mcpc-fresh"}] if source == "mcpc" else []
            ),
        ),
        patch("cyt.tools.catalog_cache.schedule_decomposed_catalog_refresh_for_sources"),
    ):
        rebuild_master_catalog(config, blocking=False)

    catalog = get_master_tool_catalog(config, blocking=False)
    assert catalog is not None
    assert len(catalog) == 2
    sources = {tool["cyt_catalog_source"] for tool in catalog}
    assert sources == {"mcpc", "executor"}


def test_rebuild_master_catalog_drops_source_when_fingerprint_changes_and_read_empty() -> None:
    config = load_config()
    config["pruning"]["inject_via"] = {"cursor": "hook", "claude": "hook", "codex": "hook"}
    config["tools"]["enabled"] = True
    config["tools"]["hook"]["tools_from"] = ["mcpc", "executor"]

    with (
        patch(
            "cyt.tools.master_catalog._load_source_tools",
            side_effect=lambda _cfg, source, **_: [{"name": f"{source}-tool"}],
        ),
        patch("cyt.tools.catalog_cache.schedule_decomposed_catalog_refresh_for_sources"),
    ):
        rebuild_master_catalog(config, blocking=True)

    with (
        patch(
            "cyt.tools.master_catalog._current_source_fingerprints",
            return_value={"mcpc": "fp-mcpc", "executor": "fp-executor-new"},
        ),
        patch(
            "cyt.tools.master_catalog._load_source_tools",
            side_effect=lambda _cfg, source, **_: (
                [{"name": "mcpc-fresh"}] if source == "mcpc" else []
            ),
        ),
        patch("cyt.tools.catalog_cache.schedule_decomposed_catalog_refresh_for_sources"),
    ):
        rebuild_master_catalog(config, blocking=False)

    catalog = get_master_tool_catalog(config, blocking=False)
    assert catalog is not None
    assert len(catalog) == 1
    assert catalog[0]["cyt_catalog_source"] == "mcpc"


def test_rebuild_master_catalog_clears_stale_tools_when_all_sources_genuinely_empty() -> None:
    config = load_config()
    config["pruning"]["inject_via"] = {"cursor": "hook", "claude": "hook", "codex": "hook"}
    config["tools"]["enabled"] = True
    config["tools"]["hook"]["tools_from"] = ["executor"]

    with (
        patch(
            "cyt.tools.master_catalog._load_source_tools",
            return_value=[{"name": "executor-tool"}],
        ),
        patch("cyt.tools.catalog_cache.schedule_decomposed_catalog_refresh_for_sources"),
    ):
        rebuild_master_catalog(config, blocking=True)

    with (
        patch(
            "cyt.tools.master_catalog._current_source_fingerprints",
            return_value={"executor": "fp-executor-new"},
        ),
        patch("cyt.tools.master_catalog._load_source_tools", return_value=[]),
        patch("cyt.tools.catalog_cache.schedule_decomposed_catalog_refresh_for_sources"),
    ):
        rebuild_master_catalog(config, blocking=False)

    state = _get_state(_cache_key_for_config(config))
    assert _snapshot_master_tools(state) == []


def test_blocking_get_master_waits_for_in_progress_rebuild() -> None:
    config = load_config()
    config["pruning"]["inject_via"] = {"cursor": "hook", "claude": "hook", "codex": "hook"}
    config["tools"]["enabled"] = True
    config["tools"]["hook"]["tools_from"] = ["mcpc"]

    def _slow_load_source_tools(*_args: object, **_kwargs: object) -> list[dict[str, str]]:
        time.sleep(0.1)
        return [{"name": "mcpc-tool"}]

    def background_rebuild() -> None:
        with (
            patch(
                "cyt.tools.master_catalog._load_source_tools",
                side_effect=_slow_load_source_tools,
            ),
            patch("cyt.tools.catalog_cache.schedule_decomposed_catalog_refresh_for_sources"),
        ):
            rebuild_master_catalog(config, blocking=True)

    thread = threading.Thread(target=background_rebuild)
    thread.start()
    time.sleep(0.01)
    catalog = get_master_tool_catalog(config, blocking=True)
    thread.join(timeout=2.0)

    assert catalog is not None
    assert len(catalog) == 1
    assert catalog[0]["name"] == "mcpc-tool"
