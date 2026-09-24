"""Gherkin steps for tools.enabled tier stats and prompt-eval gating."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from pytest_bdd import given, scenarios, then, when

from cyt.pruners.tools_filter import _tier_prune_context
from cyt.tiers.cli import main as tiers_main
from cyt.tiers.manager import _managers, get_tier_manager
from tests.support.tiers_stats_fixtures import (
    materialize_fixture_pack,
    patch_cyt_mcp_paths,
    tier_stats_config,
    write_cyt_mcp_disk_catalog,
)
from tests.unit.gherkin.conftest import GherkinContext

FEATURES = Path(__file__).resolve().parent / "features" / "tools_tier_stats_gate.feature"
scenarios(str(FEATURES))

pytestmark = pytest.mark.gherkin


@given("tools tier stats fixtures with tools.enabled false")
def given_tools_disabled_fixture_pack(
    gherkin_context: GherkinContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack = materialize_fixture_pack(tmp_path)
    patch_cyt_mcp_paths(monkeypatch, pack, tools_enabled=False)
    write_cyt_mcp_disk_catalog(pack)
    _managers.clear()
    gherkin_context.tmp_path = pack.workspace
    gherkin_context.config = tier_stats_config(pack, tools_enabled=False)
    gherkin_context.payload = {"pack": pack}


@when("cyt tiers stats runs with JSON output")
def when_tiers_stats_json(
    gherkin_context: GherkinContext,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = gherkin_context.tmp_path
    assert workspace is not None
    monkeypatch.chdir(workspace)
    code = tiers_main(["stats", "--workspace", str(workspace), "--json"])
    captured = capsys.readouterr()
    gherkin_context.payload["exit_code"] = code
    gherkin_context.stdout = captured.out
    gherkin_context.stderr = captured.err


@when("cyt tiers stats runs with text output")
def when_tiers_stats_text(
    gherkin_context: GherkinContext,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = gherkin_context.tmp_path
    assert workspace is not None
    monkeypatch.chdir(workspace)
    code = tiers_main(["stats", "--workspace", str(workspace)])
    captured = capsys.readouterr()
    gherkin_context.payload["exit_code"] = code
    gherkin_context.stdout = captured.out


@when("cyt tiers stats runs for tools kind")
def when_tiers_stats_kind_tools(
    gherkin_context: GherkinContext,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = gherkin_context.tmp_path
    assert workspace is not None
    monkeypatch.chdir(workspace)
    code = tiers_main(
        ["stats", "--workspace", str(workspace), "--kind", "tools"],
    )
    captured = capsys.readouterr()
    gherkin_context.payload["exit_code"] = code
    gherkin_context.stderr = captured.err


@when("cyt tiers stats runs for server filter")
def when_tiers_stats_server_filter(
    gherkin_context: GherkinContext,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = gherkin_context.tmp_path
    assert workspace is not None
    monkeypatch.chdir(workspace)
    code = tiers_main(
        ["stats", "--workspace", str(workspace), "--server", "context-mode"],
    )
    captured = capsys.readouterr()
    gherkin_context.payload["exit_code"] = code
    gherkin_context.stderr = captured.err


@then("tiers stats JSON should omit tools")
def then_json_omits_tools(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload.get("exit_code") == 0
    payload = json.loads(gherkin_context.stdout)
    assert "tools" not in payload
    overview = payload.get("overview") or {}
    assert "tools" not in (overview.get("tier_statistics") or {})
    assert "mcp_servers" not in overview


@then("tiers stats text should omit tools sections")
def then_text_omits_tools(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload.get("exit_code") == 0
    out = gherkin_context.stdout
    assert "=== tools ===" not in out
    assert "tools injected=" not in out
    assert "tools used=" not in out
    assert "Historical signals (decayed sum):" in out
    assert "skills injected=" in out


@then("tiers stats should report tools stats unavailable")
def then_kind_tools_errors(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload.get("exit_code") == 2
    assert "tools.enabled is false" in gherkin_context.stderr


@given("tools tier prompt eval is disabled in config")
def given_prompt_eval_disabled(gherkin_context: GherkinContext) -> None:
    gherkin_context.config = {
        "pruning": {"tools": {"enabled": False}},
        "tools": {"tiers": {"mode": "live"}},
    }


@when("tier prune context is prepared for a query")
def when_tier_prune_context(
    gherkin_context: GherkinContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apply_tools = MagicMock()
    record = MagicMock()
    fake_manager = MagicMock()
    fake_manager.apply_tools = apply_tools
    fake_manager.record_tool_candidates = record
    monkeypatch.setattr(
        "cyt.tiers.manager.get_tier_manager_for_config",
        lambda *_args, **_kwargs: fake_manager,
    )

    _tier_prune_context(
        [{"name": "search", "cyt_catalog_source": "cyt_mcp"}],
        gherkin_context.config,
        ctx=None,
        configured_pipeline=["bm25"],
        for_hook=True,
    )
    gherkin_context.payload["apply_tools"] = apply_tools
    gherkin_context.payload["record"] = record


@then("tool tier apply and candidate recording should be skipped")
def then_prompt_eval_skipped(gherkin_context: GherkinContext) -> None:
    gherkin_context.payload["apply_tools"].assert_not_called()
    gherkin_context.payload["record"].assert_not_called()


@given("a workspace with tools tier tracking enabled but injection disabled")
def given_tracking_workspace(
    gherkin_context: GherkinContext,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    db_path = workspace / "tier_state.db"
    config: dict[str, Any] = {
        "pruning": {
            "tools": {
                "enabled": False,
                "hook": {
                    "tools_from": ["cyt_mcp"],
                    "cyt_mcp": {"agent": "cursor"},
                },
            },
        },
        "tools": {"tiers": {"mode": "live", "database": {"path": str(db_path)}}},
        "skills": {"enabled": True, "tiers": {"mode": "live"}},
    }
    _managers.clear()
    gherkin_context.tmp_path = workspace
    gherkin_context.config = config


@when("a tool usage event is recorded")
def when_record_tool_used(gherkin_context: GherkinContext) -> None:
    workspace = gherkin_context.tmp_path
    assert workspace is not None
    manager = get_tier_manager(gherkin_context.config, workspace=workspace)
    manager.record_tool_used(
        {"name": "search", "cyt_catalog_source": "cyt_mcp"},
        config=gherkin_context.config,
    )
    gherkin_context.payload["manager"] = manager


@then("tier state should reflect the tool usage")
def then_tool_usage_recorded(gherkin_context: GherkinContext) -> None:
    manager = gherkin_context.payload["manager"]
    state = manager._states.get(("tool", "cyt_mcp:search"))
    assert state is not None
    assert state.stats.used == 1.0
