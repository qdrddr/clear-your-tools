"""Gherkin steps for skills.enabled tier stats and prompt-eval gating."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from pytest_bdd import given, scenarios, then, when

from cyt.tiers.adapters.skills import resolve_tiered_skill_matches
from cyt.tiers.cli import main as tiers_main
from cyt.tiers.manager import _managers, get_tier_manager
from tests.support.tiers_stats_fixtures import (
    materialize_fixture_pack,
    patch_cyt_mcp_paths,
    tier_stats_config,
    write_cyt_mcp_disk_catalog,
)
from tests.unit.gherkin.conftest import GherkinContext

FEATURES = Path(__file__).resolve().parent / "features" / "skills_tier_stats_gate.feature"
scenarios(str(FEATURES))

pytestmark = pytest.mark.gherkin


@given("skills tier stats fixtures with skills.enabled false")
def given_skills_disabled_fixture_pack(
    gherkin_context: GherkinContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack = materialize_fixture_pack(tmp_path)
    patch_cyt_mcp_paths(monkeypatch, pack, skills_enabled=False)
    write_cyt_mcp_disk_catalog(pack)
    _managers.clear()
    gherkin_context.tmp_path = pack.workspace
    gherkin_context.config = tier_stats_config(pack, skills_enabled=False)
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


@when("cyt tiers stats runs for skills kind")
def when_tiers_stats_kind_skills(
    gherkin_context: GherkinContext,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = gherkin_context.tmp_path
    assert workspace is not None
    monkeypatch.chdir(workspace)
    code = tiers_main(
        ["stats", "--workspace", str(workspace), "--kind", "skills"],
    )
    captured = capsys.readouterr()
    gherkin_context.payload["exit_code"] = code
    gherkin_context.stderr = captured.err


@then("tiers stats JSON should omit skills")
def then_json_omits_skills(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload.get("exit_code") == 0
    payload = json.loads(gherkin_context.stdout)
    assert "skills" not in payload
    overview = payload.get("overview") or {}
    assert "skills" not in (overview.get("tier_statistics") or {})


@then("tiers stats text should omit skills sections")
def then_text_omits_skills(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload.get("exit_code") == 0
    out = gherkin_context.stdout
    assert "=== skills ===" not in out
    assert "skills injected=" not in out
    assert "skills used=" not in out
    assert "Historical signals (decayed sum):" in out
    assert "tools injected=" in out


@then("tiers stats should report skills stats unavailable")
def then_kind_skills_errors(gherkin_context: GherkinContext) -> None:
    assert gherkin_context.payload.get("exit_code") == 2
    assert "skills.enabled is false" in gherkin_context.stderr


@given("skills tier prompt eval is disabled in config")
def given_prompt_eval_disabled(gherkin_context: GherkinContext) -> None:
    gherkin_context.config = {
        "skills": {"enabled": False, "tiers": {"mode": "live"}},
    }


@when("tiered skill matches are resolved for a query")
def when_resolve_tiered_matches(
    gherkin_context: GherkinContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    partition = MagicMock()
    record = MagicMock()
    fake_manager = MagicMock()
    fake_manager.partition_skills = partition
    fake_manager.record_skill_candidates = record
    search = MagicMock(return_value=[])
    monkeypatch.setattr(
        "cyt.tiers.manager.get_tier_manager",
        lambda *_args, **_kwargs: fake_manager,
    )
    monkeypatch.setattr("cyt.skills.search.search_skills", search)

    resolve_tiered_skill_matches(
        "demo query",
        [],
        config=gherkin_context.config,
        skip_frontmatter_gate=True,
    )
    gherkin_context.payload["partition"] = partition
    gherkin_context.payload["record"] = record
    gherkin_context.payload["search"] = search


@then("skill tier partition and candidate recording should be skipped")
def then_prompt_eval_skipped(gherkin_context: GherkinContext) -> None:
    gherkin_context.payload["partition"].assert_not_called()
    gherkin_context.payload["record"].assert_not_called()
    gherkin_context.payload["search"].assert_called_once()


@given("a workspace with skills tier tracking enabled but injection disabled")
def given_tracking_workspace(
    gherkin_context: GherkinContext,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    db_path = workspace / "tier_state.db"
    config: dict[str, Any] = {
        "skills": {"enabled": False, "tiers": {"mode": "live"}},
        "tools": {"tiers": {"mode": "live", "database": {"path": str(db_path)}}},
    }
    _managers.clear()
    gherkin_context.tmp_path = workspace
    gherkin_context.config = config


@when("a skill usage event is recorded")
def when_record_skill_used(gherkin_context: GherkinContext) -> None:
    workspace = gherkin_context.tmp_path
    assert workspace is not None
    manager = get_tier_manager(gherkin_context.config, workspace=workspace)
    manager.record_skill_used("skill:demo", config=gherkin_context.config)
    gherkin_context.payload["manager"] = manager


@then("tier state should reflect the skill usage")
def then_skill_usage_recorded(gherkin_context: GherkinContext) -> None:
    manager = gherkin_context.payload["manager"]
    state = manager._states.get(("skill", "skill:demo"))
    assert state is not None
    assert state.stats.used == 1.0
