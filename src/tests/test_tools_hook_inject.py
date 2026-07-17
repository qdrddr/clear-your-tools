"""Tests for tools hook injection via the hook handler."""

from __future__ import annotations

import json
import sys
import tempfile
import threading
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from cyt.proxy.anthropic import PruneResult
from cyt.skills import cli as skills_cli
from cyt.skills.executor_skill import EXECUTOR_SKILL_NAME, executor_skill_match_from_text
from cyt.skills.inject import format_skill_item
from cyt.tools.hook import _finish_tools_hook_injection


@pytest.fixture(autouse=True)
def _quiet_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CYT_HOOK_QUIET", "1")


def _tools_hook_config(root: Path, definitions: Path) -> dict[str, Any]:
    return {
        "skills": {"enabled": False},
        "pruning": {
            "inject_via": "hook",
            "tools": {
                "hook": {
                    "tools_from": "definitions",
                    "mcp_definitions_file": str(definitions),
                },
                "sequence": ["bm25"],
            },
        },
        "stats": {"database": {"path": str(root / "stats.db")}},
    }


def _hook_payload(prompt: str = "read a file") -> dict[str, Any]:
    return {
        "hook_event_name": "UserPromptSubmit",
        "prompt": prompt,
        "cwd": "/tmp/project",
        "model": "claude-sonnet-4-20250514",
    }


def test_hook_skips_tools_when_tools_disabled() -> None:
    config = {
        "skills": {"enabled": False},
        "pruning": {
            "inject_via": "hook",
            "tools": {"enabled": False},
        },
    }
    payload = _hook_payload()
    result = skills_cli.run_hook_payload(payload, config)
    assert result.stdout_text == ""
    assert result.outcome == "skipped_inject_via_proxy"


def test_hook_injects_agent_tools_block() -> None:
    fixture = Path(__file__).resolve().parent / "fixtures" / "mcp_definitions_sample.json"
    catalog = json.loads(fixture.read_text(encoding="utf-8"))["tools"]
    pruned = [catalog[0]]

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = _tools_hook_config(root, fixture)

        with (
            patch.object(skills_cli, "load_config", return_value=config),
            patch("cyt.tools.hook.load_tool_catalog", return_value=catalog),
            patch(
                "cyt.tools.hook.prune_tools_for_query",
                return_value=PruneResult(
                    tools=pruned,
                    status="applied",
                    query="read a file",
                    tools_in=2,
                    mcp_tools_in=2,
                    tools_out=1,
                    error=None,
                    tokens_in=100,
                    tokens_out=40,
                ),
            ),
            patch("cyt.tools.hook.record_tools_hook_injection"),
            patch.object(sys, "stdin", StringIO(json.dumps(_hook_payload()))),
        ):
            stdout = StringIO()
            with patch.object(sys, "stdout", stdout):
                skills_cli.run(debug=False)

        output = json.loads(stdout.getvalue())
        context = output["hookSpecificOutput"]["additionalContext"]
        assert output["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
        assert "<agent-tools" in context
        assert " path='/tmp/project'" in context
        assert "mcp__filesystem__read_file" in context


def test_hook_injects_mcpc_agent_tools_block() -> None:
    catalog = [
        {
            "name": "@ctx7/resolve-library-id",
            "tool_name": "resolve-library-id",
            "mcpc_session": "@ctx7",
            "title": "Resolve Context7 Library ID",
            "description": "Resolve a library id",
            "input_schema": {
                "type": "object",
                "properties": {
                    "libraryName": {"type": "string"},
                    "query": {"type": "string"},
                },
            },
            "server_name": "Context7",
            "server_instructions": "Use this server for docs.",
        },
    ]

    config = {
        "skills": {"enabled": False},
        "pruning": {
            "inject_via": "hook",
            "tools": {
                "enabled": True,
                "hook": {
                    "tools_from": "mcpc",
                    "mcpc": {"executable": "mcpc"},
                },
                "sequence": ["bm25"],
            },
        },
        "stats": {"database": {"path": "/tmp/stats.db"}},
    }

    with (
        patch.object(skills_cli, "load_config", return_value=config),
        patch("cyt.tools.hook.load_tool_catalog", return_value=catalog),
        patch(
            "cyt.tools.hook.prune_tools_for_query",
            return_value=PruneResult(
                tools=catalog,
                status="applied",
                query="resolve library id",
                tools_in=1,
                mcp_tools_in=1,
                tools_out=1,
                error=None,
                tokens_in=100,
                tokens_out=40,
            ),
        ),
        patch("cyt.tools.hook.record_tools_hook_injection"),
        patch.object(sys, "stdin", StringIO(json.dumps(_hook_payload("resolve library id")))),
    ):
        stdout = StringIO()
        with patch.object(sys, "stdout", stdout):
            skills_cli.run(debug=False)

    output = json.loads(stdout.getvalue())
    context = output["hookSpecificOutput"]["additionalContext"]
    assert "<agent-tools" in context
    assert "<server name='Context7'" in context
    assert "mcpc @ctx7 tools-call resolve-library-id" in context
    assert "<json-schema>" in context


def _combined_hook_config(root: Path, definitions: Path, skills_dir: Path) -> dict[str, Any]:
    return {
        "skills": {
            "enabled": True,
            "pipeline": "bm25",
            "directories": [str(skills_dir)],
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
                "hook": {
                    "tools_from": "definitions",
                    "mcp_definitions_file": str(definitions),
                },
                "sequence": ["bm25"],
            },
        },
        "stats": {"database": {"path": str(root / "stats.db")}},
    }


def test_hook_runs_skills_and_tools_injection_in_parallel() -> None:
    overlap = threading.Event()
    gate = threading.Barrier(2, action=overlap.set)

    def slow_tools(*_args: object, **_kwargs: object) -> PruneResult:
        gate.wait(timeout=2.0)
        return PruneResult(
            tools=[{"name": "mcp__x__tool"}],
            status="applied",
            query="parallel injection",
            tools_in=1,
            mcp_tools_in=1,
            tools_out=1,
            error=None,
        )

    def slow_skills(*_args: object, **_kwargs: object) -> list[dict[str, Any]]:
        gate.wait(timeout=2.0)
        return [{"skill": "one"}]

    payload = _hook_payload("parallel injection")
    config = _combined_hook_config(Path("/tmp"), Path("/tmp/definitions.json"), Path("/tmp/skills"))

    with (
        patch("cyt.config.tools_hook_file_missing", return_value=False),
        patch("cyt.pruning.coordinator.filter_tools_for_query", side_effect=slow_tools),
        patch("cyt.pruning.coordinator._run_skills_search", side_effect=slow_skills),
        patch(
            "cyt.pruning.hook_bridge.load_tool_catalog",
            return_value=[{"name": "mcp__x__tool", "description": "tool"}],
        ),
        patch(
            "cyt.pruning.hook_bridge.eligible_skills_after_gate",
            return_value=[{"path": "skill.md"}],
        ),
        patch("cyt.pruning.coordinator.effective_pruning_pipeline", return_value=["bm25"]),
        patch("cyt.pruning.coordinator.effective_skills_pipeline", return_value="bm25"),
        patch(
            "cyt.skills.cli.format_agent_skills",
            return_value="<agent-skills>skills</agent-skills>",
        ),
        patch("cyt.skills.cli.format_agent_tools", return_value="<agent-tools>tools</agent-tools>"),
    ):
        outcome, _details, _context = skills_cli._handle_user_prompt(
            payload,
            config,
            emit_stdout=False,
            io_guarded=True,
        )

    assert overlap.is_set()
    assert outcome == "user_prompt_injected"


def test_hook_skips_silently_when_catalog_missing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        missing = root / "missing.json"
        config = _tools_hook_config(root, missing)

        with (
            patch.object(skills_cli, "load_config", return_value=config),
            patch.object(sys, "stdin", StringIO(json.dumps(_hook_payload()))),
        ):
            stdout = StringIO()
            stderr = StringIO()
            with patch.object(sys, "stdout", stdout), patch.object(sys, "stderr", stderr):
                skills_cli.run(debug=False)

        assert stdout.getvalue().strip() == ""
        assert stderr.getvalue().strip() == ""


def test_finish_tools_hook_injection_does_not_append_executor_skill() -> None:
    config = {
        "pruning": {
            "inject_via": "hook",
            "tools": {"hook": {"tools_from": "executor", "executor_url": "http://localhost:4789"}},
        },
    }
    _outcome, _details, injected = _finish_tools_hook_injection(
        payload={"prompt": "demo"},
        config=config,
        query="demo",
        model="hook",
        result=PruneResult(
            tools=[{"name": "tools.demo.tool"}],
            status="applied",
            query="demo",
            tools_in=1,
            mcp_tools_in=1,
            tools_out=1,
            error=None,
        ),
        catalog=[{"name": "tools.demo.tool"}],
        injected="<agent-tools>\n<tool name='tools.demo.tool'>{'input_schema':{}}</tool>\n</agent-tools>",
        request_tokens=10,
        budget_debug={},
        debug=False,
    )
    assert f'<skill name="{EXECUTOR_SKILL_NAME}"' not in injected
    assert "<agent-tools>" in injected


def test_filter_pre_exposed_skills_drops_executor_skill_fragment() -> None:
    from cyt.injection.pre_exposed import filter_pre_exposed_skills
    from cyt.skills.inject import format_agent_skills

    match = executor_skill_match_from_text("# execute\n\nUse tools.search()")
    assert match is not None
    fragment = format_skill_item(match)
    session_text = fragment
    filtered = filter_pre_exposed_skills([match], session_text)
    assert filtered == []
    assert format_agent_skills(filtered) == ""
