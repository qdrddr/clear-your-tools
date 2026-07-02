"""Tests for tools hook injection via cyt hook --stdin."""

from __future__ import annotations

import json
import sys
import tempfile
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from cyt.proxy.anthropic import PruneResult
from cyt.skills import cli as skills_cli


@pytest.fixture(autouse=True)
def _quiet_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CYT_HOOK_QUIET", "1")


def _tools_hook_config(root: Path, definitions: Path) -> dict[str, Any]:
    return {
        "skills": {"enabled": False},
        "pruning": {
            "tools": {
                "inject_via": "hook",
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
        assert "<agent-tools>" in context
        assert "mcp__filesystem__read_file" in context


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
