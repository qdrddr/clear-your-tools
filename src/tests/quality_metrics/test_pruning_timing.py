"""Tests for hook/prune phase timing and worker config."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest

from cyt.common.phase_timing import PhaseTimer, extend_timing_payload
from cyt.config import max_prune_batch_workers
from cyt.skills.cli import format_hook_stdout
from cyt.tools.catalog_cache import (
    _DecomposedCatalogState,
    _warm_prepared_selector,
    clear_decomposed_catalog_cache,
    get_prepared_selector_chunks,
)


def test_phase_timer_records_elapsed_ms() -> None:
    timer = PhaseTimer()
    with timer.measure("catalog", bulk_id="mcpc"):
        pass
    payload = timer.to_dict()
    assert payload["phases"][0]["name"] == "catalog"
    assert payload["phases"][0]["meta"]["bulk_id"] == "mcpc"
    assert payload["total_ms"] >= 0


def test_extend_timing_payload_merges_phases() -> None:
    base = {"total_ms": 5, "phases": [{"name": "catalog", "elapsed_ms": 5}]}
    extra = PhaseTimer()
    with extra.measure("gate:tools"):
        pass
    merged = extend_timing_payload(base, extra)
    names = [phase["name"] for phase in merged["phases"]]
    assert names == ["catalog", "gate:tools"]


def test_max_prune_batch_workers_from_config() -> None:
    config: dict[str, Any] = {"pruning": {"max_batch_workers": 9}}
    assert max_prune_batch_workers(config) == 9


def test_max_prune_batch_workers_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CYT_MAX_PRUNE_BATCH_WORKERS", "12")
    assert max_prune_batch_workers({"pruning": {"max_batch_workers": 3}}) == 12


def test_format_hook_stdout_includes_phase_timing() -> None:
    payload = {"hook_event_name": "beforeSubmitPrompt"}
    stdout = format_hook_stdout(
        "ctx",
        payload,
        phase_timing={"total_ms": 10, "phases": [{"name": "hook:e2e", "elapsed_ms": 10}]},
    )
    data = json.loads(stdout)
    assert data["cytPhaseTiming"]["phases"][0]["name"] == "hook:e2e"


def test_prepared_selector_cache_hit_on_full_catalog() -> None:
    clear_decomposed_catalog_cache()
    catalog = {
        "json": [{"file_path": "a.py", "name": "tool_a", "score": 0.0}],
        "md": [{"file_path": "b.md", "name": "chunk_b", "score": 0.0}],
    }
    state = _DecomposedCatalogState(bulk_id="mcpc")
    _warm_prepared_selector(state, catalog)
    from cyt.tools import catalog_cache

    cache_key = catalog_cache._cache_key("mcpc", {"cache": {"tools_dir": "/tmp/cyt-tools"}})
    with catalog_cache._decomposed_lock:
        catalog_cache._decomposed_states[cache_key] = state

    prepared = get_prepared_selector_chunks(
        catalog,
        bulk_id="mcpc",
        config={"cache": {"tools_dir": "/tmp/cyt-tools"}},
    )
    assert prepared is not None
    chunks, _meta, _keys, _counts, _rows = prepared
    assert chunks
    assert "<tool id=1" in chunks[0]

    trimmed = {"json": catalog["json"], "md": []}
    assert (
        get_prepared_selector_chunks(
            trimmed,
            bulk_id="mcpc",
            config={"cache": {"tools_dir": "/tmp/cyt-tools"}},
        )
        is None
    )


def test_build_and_swap_clears_stale_prepared_selector_when_warm_fails() -> None:
    clear_decomposed_catalog_cache()
    config = {"cache": {"tools_dir": "/tmp/cyt-tools"}}
    old_catalog = {
        "json": [{"file_path": "old.py", "name": "tool_old", "score": 0.0}],
        "md": [],
    }
    new_catalog = {
        "json": [{"file_path": "new.py", "name": "tool_new", "score": 0.0}],
        "md": [],
    }
    state = _DecomposedCatalogState(bulk_id="mcpc")
    _warm_prepared_selector(state, old_catalog)
    from cyt.tools import catalog_cache

    cache_key = catalog_cache._cache_key("mcpc", config)
    with catalog_cache._decomposed_lock:
        state.catalog = old_catalog
        catalog_cache._decomposed_states[cache_key] = state

    entries = [{"name": "tool_new", "description": "N", "input_schema": {"type": "object"}}]
    with (
        patch(
            "cyt.tools.catalog_cache.ensure_tool_catalog_from_entries",
            return_value={
                "catalog": new_catalog,
                "index": {"tools": [], "files": {}},
                "cache_status": "memory_fallback",
                "disk_backed": False,
            },
        ),
        patch(
            "cyt.pruners.llm.prepare_catalog_selector_chunks",
            side_effect=RuntimeError("warm failed"),
        ),
    ):
        catalog_cache._build_and_swap("mcpc", entries, [], config, "fp-new")

    assert (
        get_prepared_selector_chunks(
            new_catalog,
            bulk_id="mcpc",
            config=config,
        )
        is None
    )
