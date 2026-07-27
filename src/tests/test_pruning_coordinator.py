"""Tests for shared skills+tools pruning coordinator."""

from __future__ import annotations

import threading
import time
from typing import Any, cast
from unittest.mock import patch

from cyt.config import effective_skills_pipeline
from cyt.proxy.anthropic import PruneResult
from cyt.pruning.context import MAX_PRUNE_BATCH_WORKERS, PruneContext
from cyt.pruning.coordinator import (
    ToolSource,
    build_prune_plan,
    coordinate_skills_tools_prune,
)
from cyt.pruning.parallel import run_parallel


def test_effective_skills_pipeline_falls_back_to_bm25_below_threshold() -> None:
    config: dict[str, Any] = {
        "skills": {
            "pipeline": "rerank",
            "bm25_node_fallback_threshold": 10,
        },
    }
    assert effective_skills_pipeline(config, eligible_count=3) == "bm25"
    assert effective_skills_pipeline(config, eligible_count=20) == "rerank"


def test_build_plan_both_bm25_parallel_stage() -> None:
    ctx = PruneContext(
        query="find files",
        config={},
        tools_effective=["bm25"],
        skills_effective="bm25",
        skills_allowed=True,
        tools_allowed=True,
    )
    plan = build_prune_plan(ctx, tool_sources=[ToolSource("root", [])])
    assert len(plan) == 1
    kinds = {unit.kind for unit in plan[0]}
    assert kinds == {"tools_pipeline", "skills_search"}


def test_build_plan_both_rerank_parallel_stage() -> None:
    ctx = PruneContext(
        query="find files",
        config={},
        tools_effective=["rerank"],
        skills_effective="rerank",
        skills_allowed=True,
        tools_allowed=True,
    )
    plan = build_prune_plan(ctx, tool_sources=[ToolSource("root", [])])
    assert len(plan) == 1
    kinds = {unit.kind for unit in plan[0]}
    assert kinds == {"tools_pipeline", "skills_search"}


def test_build_plan_both_llm_parallel_stage() -> None:
    ctx = PruneContext(
        query="find files",
        config={},
        tools_effective=["llm"],
        skills_effective="llm",
        skills_allowed=True,
        tools_allowed=True,
    )
    plan = build_prune_plan(ctx, tool_sources=[ToolSource("root", [])])
    assert len(plan) == 1
    kinds = {unit.kind for unit in plan[0]}
    assert kinds == {"tools_pipeline", "skills_search"}


def test_build_plan_bm25_tools_rerank_skills_single_parallel_stage() -> None:
    ctx = PruneContext(
        query="find files",
        config={},
        tools_effective=["bm25", "rerank"],
        skills_effective="rerank",
        skills_allowed=True,
        tools_allowed=True,
    )
    plan = build_prune_plan(ctx, tool_sources=[ToolSource("root", [])])
    assert len(plan) == 1
    kinds = {unit.kind for unit in plan[0]}
    assert kinds == {"tools_pipeline", "skills_search"}
    tool_unit = next(unit for unit in plan[0] if unit.kind == "tools_pipeline")
    assert tool_unit.pipeline == ("bm25", "rerank")


def test_run_parallel_runs_concurrently() -> None:
    overlap = threading.Event()
    gate = threading.Barrier(2, action=overlap.set)

    def slow(name: str) -> str:
        gate.wait(timeout=2.0)
        return name

    results = run_parallel(
        {"a": lambda: slow("a"), "b": lambda: slow("b")},
        max_workers=MAX_PRUNE_BATCH_WORKERS,
    )

    assert overlap.is_set()
    assert results == {"a": "a", "b": "b"}


def test_coordinate_both_bm25_runs_in_parallel() -> None:
    overlap = threading.Event()
    gate = threading.Barrier(2, action=overlap.set)

    def slow_tools(*_args: object, **_kwargs: object) -> PruneResult:
        gate.wait(timeout=2.0)
        return PruneResult(
            tools=[{"name": "tool_a"}],
            status="applied",
            query="q",
            tools_in=1,
            mcp_tools_in=1,
            tools_out=1,
            error=None,
        )

    def slow_skills(*_args: object, **_kwargs: object) -> list[dict[str, Any]]:
        gate.wait(timeout=2.0)
        return []

    config: dict[str, Any] = {
        "skills": {"enabled": True, "pipeline": "bm25"},
        "pruning": {"inject_via": "hook", "tools": {"sequence": ["bm25"]}},
    }
    tools = [{"name": "tool_a", "description": "a"}]

    with (
        patch("cyt.pruning.coordinator.filter_tools_for_query", side_effect=slow_tools),
        patch("cyt.pruning.coordinator._run_skills_search", side_effect=slow_skills),
        patch("cyt.pruning.coordinator.effective_pruning_pipeline", return_value=["bm25"]),
        patch("cyt.pruning.coordinator.effective_skills_pipeline", return_value="bm25"),
    ):
        result = coordinate_skills_tools_prune(
            "query",
            config,
            [ToolSource("root", tools)],
            skill_entries=[{"path": "skill.md"}],
            for_hook=True,
            skills_allowed=True,
            tools_allowed=True,
        )

    assert overlap.is_set()
    assert "root" in result.prune_results


def test_coordinated_hook_applies_executor_tool_kind() -> None:
    from cyt.indexer.policies import PolicyContext

    config: dict[str, Any] = {
        "skills": {"enabled": True, "pipeline": "bm25"},
        "pruning": {
            "inject_via": "hook",
            "tools": {
                "hook": {"tools_from": "executor"},
                "sequence": ["bm25"],
            },
        },
    }
    tools = [{"name": "tools.demo.org.default.search", "description": "search"}]
    captured: list[PolicyContext] = []

    def capture_run_catalog_pruning(*args: object, **kwargs: object) -> tuple:
        captured.append(args[5])
        captured.append(args[6])
        raise RuntimeError("stop-after-context")

    with (
        patch(
            "cyt.pruners.tools_filter._run_catalog_pruning",
            side_effect=capture_run_catalog_pruning,
        ),
        patch("cyt.pruning.coordinator._run_skills_search", return_value=[]),
        patch("cyt.config.effective_pruning_pipeline", return_value=["bm25"]),
        patch("cyt.config.effective_skills_pipeline", return_value="bm25"),
    ):
        try:
            coordinate_skills_tools_prune(
                "search",
                config,
                [ToolSource("root", tools)],
                skill_entries=[{"path": "skill.md"}],
                for_hook=True,
                skills_allowed=True,
                tools_allowed=True,
            )
        except RuntimeError as exc:
            assert str(exc) == "stop-after-context"

    assert len(captured) == 2
    assert captured[0].tool_kind == "mcp"
    assert captured[1].tool_kind == "mcp"


def test_rerank_multi_bulk_uses_parallel_runner() -> None:
    from cyt.common.token_usage import empty_usage
    from cyt.pruners import rerank as rerank_module

    calls: list[int] = []

    def fake_single_bulk(
        bulk: list[tuple[int, str]],
        *,
        query: str,
        settings: object,
        items: list[dict[str, Any]],
    ) -> tuple[Any, bool, None]:
        del query, settings, items
        calls.append(len(bulk))
        time.sleep(0.05)
        return empty_usage(), True, None

    indexed = [(index, f"doc {index}") for index in range(6)]
    items = [{"score": "0.0"} for _ in range(6)]

    with (
        patch.object(
            rerank_module,
            "split_into_bulks",
            return_value=[[indexed[0], indexed[1]], [indexed[2], indexed[3]]],
        ),
        patch.object(rerank_module, "_rerank_single_bulk", side_effect=fake_single_bulk),
    ):
        started = time.perf_counter()
        rerank_module._rerank_prepared_bulks(
            indexed,
            query="q",
            settings=cast(Any, object()),
            items=items,
            base_tokens=10,
            min_score=None,
        )
        elapsed = time.perf_counter() - started

    assert len(calls) == 2
    assert elapsed < 0.12
