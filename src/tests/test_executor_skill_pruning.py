"""Tests for executor execute skill pruning through the hook pipeline."""

from __future__ import annotations

import json
import sys
import tempfile
import time
from io import StringIO
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest

from cyt.proxy.anthropic import PruneResult
from cyt.pruning.coordinator import build_prune_plan, prepare_prune_context
from cyt.skills import cli as skills_cli
from cyt.skills.executor_skill import (
    EXECUTOR_SKILL_NAME,
    _executor_registry_content,
    append_executor_skill_entries,
    build_executor_skill_registry,
    executor_skill_inline_source,
)
from cyt.skills.search import MatchedSkill

_EXECUTOR_SKILL_MD = (
    "# execute\n\n"
    "## Workflow\n\n"
    "Use tools.search() to find matching tools.\n\n"
    "## Unrelated Topic\n\n"
    "Knitting patterns and yarn colors for winter scarves.\n"
)

_EXECUTOR_CONFIG: dict[str, Any] = {
    "skills": {
        "enabled": False,
        "pipeline": "bm25",
        "max_tokens_per_request": 4000,
        "pageindex": {"enable_bm25_chunking": True},
        "hook": {
            "request_budget_fraction": 50.0,
            "inject_cap_multiplier_of_request_tokens": 5.0,
        },
    },
    "pruning": {
        "inject_via": "hook",
        "tools": {
            "enabled": True,
            "hook": {
                "tools_from": "executor",
                "executor_url": "http://localhost:4789",
                "executor_token_var": "EXECUTOR_TOKEN",
            },
            "sequence": ["bm25"],
            "pipelines": {"bm25": {"score_skills": 0.5}},
        },
    },
}


def _hook_payload(prompt: str = "tools.search workflow find tools") -> dict[str, Any]:
    return {
        "hook_event_name": "UserPromptSubmit",
        "prompt": prompt,
        "cwd": "/tmp/project",
        "model": "claude-sonnet-4-20250514",
    }


def _mock_executor_mcp_cache() -> dict[str, str]:
    return {"execute_skill": _EXECUTOR_SKILL_MD}


def _mock_prune_result(prompt: str, catalog: list[dict[str, Any]]) -> PruneResult:
    return PruneResult(
        tools=catalog,
        status="applied",
        query=prompt,
        tools_in=len(catalog),
        mcp_tools_in=len(catalog),
        tools_out=len(catalog),
        error=None,
    )


def _config_with_catalog(root: Path) -> dict[str, Any]:
    config = cast(dict[str, Any], json.loads(json.dumps(_EXECUTOR_CONFIG)))
    config["skills"]["catalog_dir"] = str(root / "catalog")
    config["cache"] = {"skills_dir": str(root / "catalog")}
    return config


@pytest.fixture(autouse=True)
def _quiet_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CYT_HOOK_QUIET", "1")


def test_executor_skill_inline_source_reads_cache() -> None:
    with patch(
        "cyt.executor.http.get_executor_mcp_cache",
        return_value={"execute_skill": _EXECUTOR_SKILL_MD},
    ):
        source = executor_skill_inline_source(_EXECUTOR_CONFIG)
    assert source is not None
    assert source["path"] == "executor/execute"
    assert source["content"] == _executor_registry_content(_EXECUTOR_SKILL_MD)
    assert source["content_sha256"]


def test_build_executor_skill_registry_decomposes_inline() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _config_with_catalog(root)
        with patch(
            "cyt.executor.http.get_executor_mcp_cache",
            return_value={"execute_skill": _EXECUTOR_SKILL_MD},
        ):
            entries = build_executor_skill_registry(config)
        assert len(entries) == 1
        entry = entries[0]
        assert entry.doc_id == "executor"
        assert (Path(entry.entry_dir) / "nodes" / "page_index.json").is_file()


def test_append_executor_skill_entries_dedupes_by_doc_id() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _config_with_catalog(root)
        with patch(
            "cyt.executor.http.get_executor_mcp_cache",
            return_value={"execute_skill": _EXECUTOR_SKILL_MD},
        ):
            executor_entries = build_executor_skill_registry(config)
            merged = append_executor_skill_entries(executor_entries, config)
        assert len(merged) == len(executor_entries)


def test_build_prune_plan_runs_skills_search_for_executor_only_entries() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _config_with_catalog(root)
        with patch(
            "cyt.executor.http.get_executor_mcp_cache",
            return_value={"execute_skill": _EXECUTOR_SKILL_MD},
        ):
            entries = build_executor_skill_registry(config)
        ctx = prepare_prune_context(
            "tools.search workflow",
            config,
            tool_count=1,
            eligible_count=len(entries),
            skill_entries=entries,
            skills_allowed=False,
            tools_allowed=True,
            for_hook=True,
        )
        assert ctx is not None
        plan = build_prune_plan(ctx, tool_sources=[])
        kinds = [unit.kind for stage in plan for unit in stage]
        assert "skills_search" in kinds


def test_executor_skill_pruned_when_skills_disabled() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _config_with_catalog(root)
        catalog = [{"name": "tools.demo.tool", "description": "Demo tool", "input_schema": {}}]
        payload = _hook_payload("tools.search workflow find matching tools")

        with (
            patch("cyt.config.tools_hook_file_missing", return_value=False),
            patch(
                "cyt.executor.http.get_executor_mcp_cache",
                return_value=_mock_executor_mcp_cache(),
            ),
            patch("cyt.pruning.hook_bridge.load_tool_catalog", return_value=catalog),
            patch(
                "cyt.pruning.coordinator.filter_tools_for_query",
                return_value=_mock_prune_result(payload["prompt"], catalog),
            ),
            patch("cyt.tools.hook.record_tools_hook_injection"),
        ):
            outcome, _details, context = skills_cli._handle_user_prompt(
                payload,
                config,
                emit_stdout=False,
                io_guarded=True,
            )

        assert outcome == "user_prompt_injected"
        assert f'<skill name="{EXECUTOR_SKILL_NAME}"' in context
        assert "tools.search()" in context
        assert "Knitting patterns" not in context
        assert _EXECUTOR_SKILL_MD not in context


def test_executor_skill_not_injected_when_no_survivors() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _config_with_catalog(root)
        config["pruning"]["tools"]["pipelines"]["bm25"]["score_skills"] = 99.0
        catalog = [{"name": "tools.demo.tool", "description": "Demo tool", "input_schema": {}}]
        payload = _hook_payload("completely unrelated knitting scarves yarn")

        with (
            patch("cyt.config.tools_hook_file_missing", return_value=False),
            patch(
                "cyt.executor.http.get_executor_mcp_cache",
                return_value=_mock_executor_mcp_cache(),
            ),
            patch("cyt.pruning.hook_bridge.load_tool_catalog", return_value=catalog),
            patch(
                "cyt.pruning.coordinator.filter_tools_for_query",
                return_value=_mock_prune_result(payload["prompt"], catalog),
            ),
            patch("cyt.tools.hook.record_tools_hook_injection"),
        ):
            outcome, _details, context = skills_cli._handle_user_prompt(
                payload,
                config,
                emit_stdout=False,
                io_guarded=True,
            )

        assert outcome == "user_prompt_injected"
        assert f'<skill name="{EXECUTOR_SKILL_NAME}"' not in context
        assert "<agent-tools" in context


def test_coordinator_runs_parallel_skills_search_for_executor_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    overlap = False
    active = {"count": 0}
    lock = __import__("threading").Lock()
    mock_sleep_s = 0.12

    def slow_tools(*_args: object, **_kwargs: object) -> PruneResult:
        nonlocal overlap
        with lock:
            active["count"] += 1
            if active["count"] == 2:
                overlap = True
        time.sleep(mock_sleep_s)
        return PruneResult(
            tools=[{"name": "tools.demo.tool"}],
            status="applied",
            query="tools.search workflow",
            tools_in=1,
            mcp_tools_in=1,
            tools_out=1,
            error=None,
        )

    def slow_skills(*_args: object, **_kwargs: object) -> list[MatchedSkill]:
        nonlocal overlap
        with lock:
            active["count"] += 1
            if active["count"] == 2:
                overlap = True
        time.sleep(mock_sleep_s)
        return [
            MatchedSkill(
                doc_id="executor",
                file_path="executor/execute",
                markdown="## Workflow\n\nUse tools.search()",
                name="executor",
                score=0.9,
                token_count=10,
            ),
        ]

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _config_with_catalog(root)
        payload = _hook_payload("tools.search workflow")
        with (
            patch(
                "cyt.executor.http.get_executor_mcp_cache",
                return_value={"execute_skill": _EXECUTOR_SKILL_MD},
            ),
            patch("cyt.pruning.coordinator.filter_tools_for_query", side_effect=slow_tools),
            patch("cyt.pruning.coordinator._run_skills_search", side_effect=slow_skills),
            patch(
                "cyt.pruning.hook_bridge.load_tool_catalog",
                return_value=[{"name": "tools.demo.tool", "description": "tool"}],
            ),
            patch("cyt.config.effective_pruning_pipeline", return_value=["bm25"]),
            patch("cyt.config.effective_skills_pipeline", return_value="bm25"),
            patch(
                "cyt.skills.cli.format_agent_skills",
                return_value="<agent-skills>executor</agent-skills>",
            ),
            patch(
                "cyt.skills.cli.gate_and_format_hook_tools",
                return_value="<agent-tools>tools</agent-tools>",
            ),
        ):
            started = time.perf_counter()
            outcome, _details, _context = skills_cli._handle_user_prompt(
                payload,
                config,
                emit_stdout=False,
                io_guarded=True,
            )
            elapsed = time.perf_counter() - started

        assert overlap
        assert outcome == "user_prompt_injected"
        assert elapsed < mock_sleep_s * 2.5


def test_required_executor_skill_env_var_names_for_llm_pipeline() -> None:
    from cyt.config import required_executor_skill_env_var_names

    config = {
        "skills": {"enabled": False, "pipeline": "llm"},
        "pruning": {
            "inject_via": "hook",
            "tools": {"hook": {"tools_from": "executor"}},
            "pipelines": {"llm": {"model_nick": "mercury-2"}},
        },
        "models": {
            "llms": {
                "remote": [
                    {
                        "name": "mercury-2",
                        "provider_nick": "inception",
                        "nick": "mercury-2",
                        "api_key_var": "INCEPTION_API_KEY",  # pragma: allowlist secret
                    },
                ],
            },
        },
    }
    names = required_executor_skill_env_var_names(config)
    assert names


def test_hook_stdout_injects_pruned_executor_skill() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _config_with_catalog(root)
        catalog = [{"name": "tools.demo.tool", "description": "Demo tool", "input_schema": {}}]

        with (
            patch.object(skills_cli, "load_config", return_value=config),
            patch("cyt.config.tools_hook_file_missing", return_value=False),
            patch(
                "cyt.executor.http.get_executor_mcp_cache",
                return_value=_mock_executor_mcp_cache(),
            ),
            patch("cyt.pruning.hook_bridge.load_tool_catalog", return_value=catalog),
            patch(
                "cyt.pruning.coordinator.filter_tools_for_query",
                return_value=_mock_prune_result("tools.search workflow", catalog),
            ),
            patch("cyt.tools.hook.record_tools_hook_injection"),
            patch.object(sys, "stdin", StringIO(json.dumps(_hook_payload()))),
        ):
            stdout = StringIO()
            with patch.object(sys, "stdout", stdout):
                skills_cli.run(debug=False)

        output = json.loads(stdout.getvalue())
        context = output["hookSpecificOutput"]["additionalContext"]
        assert f'<skill name="{EXECUTOR_SKILL_NAME}"' in context
        assert "Knitting patterns" not in context
