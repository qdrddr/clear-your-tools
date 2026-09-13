"""BM25 pruning regression with live tier filtering (T0 exclusion + T4 merge)."""

from __future__ import annotations

import copy
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from cyt.config import bm25_prune_enums, bm25_score_tool, bm25_score_tool_enum
from cyt.indexer.tokens import count_json_tokens
from cyt.pruners.tools_filter import filter_tools_for_query
from cyt.tiers.manager import _managers
from cyt_core.types.prune import PruneResult
from tests.support.bm25_tier_fixtures import (
    TIERED_OUTPUT_DIR,
    Bm25TierRunContext,
    Bm25TierScenario,
    load_bm25_tier_scenarios,
    load_scenario_input,
    materialize_bm25_tier_run,
    prepare_bm25_tier_run,
    scenario_input_path,
    scenario_output_path,
)

REGENERATE_ENV = "CYT_REGENERATE_BM25_TIER_GOLDENS"


@pytest.fixture(autouse=True)
def clear_tier_managers() -> Iterator[None]:
    _managers.clear()
    yield
    _managers.clear()


def _normalize_tool(tool: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": tool.get("name"),
        "description": tool.get("description"),
        "cyt_catalog_source": tool.get("cyt_catalog_source"),
        "input_schema": tool.get("input_schema") or tool.get("inputSchema"),
    }
    if "server_key" in tool:
        out["server_key"] = tool.get("server_key")
    if "tool_name" in tool:
        out["tool_name"] = tool.get("tool_name")
    return out


def _normalize_pruned_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = [_normalize_tool(t) for t in tools]
    normalized.sort(key=lambda item: str(item.get("name") or ""))
    return normalized


def _build_output_payload(
    *,
    scenario: Bm25TierScenario,
    golden: dict[str, Any],
    result: PruneResult,
    pruned_tools: list[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any]:
    return {
        "scenario_id": scenario.id,
        "base_golden": scenario.base_golden,
        "tool_tiers": dict(scenario.tool_tiers),
        "pruned_tools": pruned_tools,
        "query": result.query,
        "status": result.status,
        "tools_in": result.tools_in,
        "tools_out": result.tools_out,
        "score_tool": bm25_score_tool(config),
        "score_tool_enum": bm25_score_tool_enum(config),
        "prune_enums": bm25_prune_enums(config),
        "tokens_in": result.tokens_in,
        "tokens_out": result.tokens_out,
        "tokens_saved": result.tokens_saved,
        "tokenizer": golden.get("tokenizer", "tiktoken_compact_json"),
        "optional_properties_in": result.tool_properties_count_in,
        "optional_properties_out": result.tool_properties_count_out,
        "decomposed": result.decomposed,
        "must_include_tools": sorted(scenario.must_include_tools),
        "must_exclude_tools": sorted(scenario.must_exclude_tools),
    }


def _write_golden_output(scenario_id: str, payload: dict[str, Any]) -> Path:
    TIERED_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = scenario_output_path(scenario_id)
    out_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return out_path


def _run_tiered_prune(
    ctx: Bm25TierRunContext,
    config: dict[str, Any],
) -> tuple[PruneResult, dict[str, Any]]:
    golden = ctx.golden_params
    query = str(golden["query"])
    result = filter_tools_for_query(
        copy.deepcopy(ctx.tools),
        query,
        ["bm25"],
        config=config,
        for_hook=True,
        catalog_bulk_id="cyt_mcp",
    )
    return result, config


def test_tiered_scenarios_json_matches_input_files() -> None:
    for scenario in load_bm25_tier_scenarios():
        input_payload = load_scenario_input(scenario.id)
        assert input_payload["scenario_id"] == scenario.id
        assert input_payload["base_golden"] == scenario.base_golden
        assert input_payload["tool_tiers"] == scenario.tool_tiers


@pytest.mark.parametrize(
    "scenario",
    load_bm25_tier_scenarios(),
    ids=[scenario.id for scenario in load_bm25_tier_scenarios()],
)
def test_cyt_mcp_catalog_bm25_tier_prune_matches_golden(
    scenario: Bm25TierScenario,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live tiers: BM25 + tier filter + T4 merge must match checked-in golden output."""
    assert scenario_input_path(scenario.id).is_file()
    ctx = materialize_bm25_tier_run(scenario, tmp_path)
    golden = ctx.golden_params
    monkeypatch.setenv("HOME", str(tmp_path))
    _, config = prepare_bm25_tier_run(ctx, tmp_path, monkeypatch)

    result, config = _run_tiered_prune(ctx, config)

    assert result.status == golden["status"]
    assert result.tools_in == golden["tools_in"]
    assert bm25_score_tool(config) == float(golden["score_tool"])
    assert bm25_score_tool_enum(config) == float(golden["score_tool_enum"])
    assert bm25_prune_enums(config) == golden["prune_enums"]
    assert golden["tokenizer"] == "tiktoken_compact_json"
    assert result.tools is not None

    actual = _normalize_pruned_tools(result.tools)
    kept_names = {str(t.get("name")) for t in actual}
    for tool_name in scenario.must_exclude_tools:
        assert tool_name not in kept_names
    for tool_name in scenario.must_include_tools:
        assert tool_name in kept_names

    output_payload = _build_output_payload(
        scenario=scenario,
        golden=golden,
        result=result,
        pruned_tools=actual,
        config=config,
    )
    _write_golden_output(scenario.id, output_payload)

    regenerate = os.environ.get(REGENERATE_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    golden_path = scenario_output_path(scenario.id)
    if regenerate or not golden_path.is_file():
        _write_golden_output(scenario.id, output_payload)
        if regenerate:
            pytest.skip(f"regenerated golden: {golden_path.name}")

    expected = json.loads(golden_path.read_text(encoding="utf-8"))
    assert actual == expected["pruned_tools"]
    assert result.tools_out == expected["tools_out"]
    assert result.tokens_out == expected["tokens_out"]
    assert result.tokens_saved == expected["tokens_saved"]
    assert result.tool_properties_count_out == expected["optional_properties_out"]
    assert count_json_tokens(result.tools) == expected["tokens_out"]

    for tool_name in expected.get("must_exclude_tools", []):
        assert str(tool_name) not in kept_names
    for tool_name in expected.get("must_include_tools", []):
        assert str(tool_name) in kept_names
