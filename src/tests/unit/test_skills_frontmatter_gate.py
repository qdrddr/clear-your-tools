"""Unit tests for BM25 frontmatter gate (prompt vs skill description dedup)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from cyt.skills.bm25 import excluded_by_frontmatter_gate, frontmatter_gate_trace
from cyt.skills.catalog import SkillEntryRef, clear_registry_cache
from cyt.skills.client_skills import build_registry_for_hook_payload
from cyt.skills.search import eligible_skills_after_gate, search_skills
from tests.support.skills_frontmatter_gate_fixtures import (
    FIXTURES_ROOT,
    FrontmatterGateFixturePack,
    FrontmatterGateScenario,
    build_registry_from_fixture_pack,
    client_payload_for_fixture_pack,
    load_scenarios,
    materialize_fixture_pack,
    skills_gate_config,
)


@pytest.fixture
def fixture_pack(tmp_path: Path) -> FrontmatterGateFixturePack:
    return materialize_fixture_pack(tmp_path)


def _doc_ids(entries: Sequence[SkillEntryRef]) -> set[str]:
    return {entry.doc_id for entry in entries}


def test_fixture_files_exist() -> None:
    assert (FIXTURES_ROOT / "create-hook.md").is_file()
    assert (FIXTURES_ROOT / "database-shards.md").is_file()
    assert (FIXTURES_ROOT / "context7.md").is_file()
    assert (FIXTURES_ROOT / "scenarios.json").is_file()


@pytest.mark.parametrize(
    "scenario_id",
    [scenario.id for scenario in load_scenarios()[1]],
    ids=[scenario.id for scenario in load_scenarios()[1]],
)
def test_eligible_skills_after_gate_matches_fixture_scenarios(
    fixture_pack: FrontmatterGateFixturePack,
    scenario_id: str,
) -> None:
    _, scenarios = load_scenarios()
    scenario = next(item for item in scenarios if item.id == scenario_id)
    config = skills_gate_config(
        workspace=fixture_pack.workspace,
        catalog_dir=fixture_pack.catalog_dir,
        skills_dir=fixture_pack.skills_dir,
        frontmatter_upper_limit=fixture_pack.frontmatter_upper_limit,
    )
    entries = build_registry_from_fixture_pack(fixture_pack)

    eligible = eligible_skills_after_gate(scenario.query, entries, config=config)
    eligible_ids = _doc_ids(eligible)
    all_ids = _doc_ids(entries)

    assert eligible_ids == scenario.eligible_doc_ids
    assert eligible_ids == all_ids - scenario.blocked_doc_ids


@pytest.mark.parametrize(
    "scenario_id",
    [scenario.id for scenario in load_scenarios()[1]],
    ids=[scenario.id for scenario in load_scenarios()[1]],
)
def test_frontmatter_gate_trace_marks_blocked_scores(
    fixture_pack: FrontmatterGateFixturePack,
    scenario_id: str,
) -> None:
    _, scenarios = load_scenarios()
    scenario = next(item for item in scenarios if item.id == scenario_id)
    config = skills_gate_config(
        workspace=fixture_pack.workspace,
        catalog_dir=fixture_pack.catalog_dir,
        skills_dir=fixture_pack.skills_dir,
        frontmatter_upper_limit=fixture_pack.frontmatter_upper_limit,
    )
    entries = build_registry_from_fixture_pack(fixture_pack)

    rows, upper = frontmatter_gate_trace(scenario.query, entries, config=config)
    assert upper == fixture_pack.frontmatter_upper_limit

    blocked = {row.doc_id for row in rows if not row.passed}
    assert blocked == scenario.blocked_doc_ids

    for row in rows:
        if row.doc_id in scenario.blocked_doc_ids:
            assert row.passed is False
            assert row.score is not None
            assert row.score >= upper
        elif row.score is not None:
            assert row.score < upper


def test_excluded_by_frontmatter_gate_returns_entry_keys(
    fixture_pack: FrontmatterGateFixturePack,
) -> None:
    scenario = FrontmatterGateScenario(
        id="manual",
        query="create-hook agent hooks for claude code",
        blocked_doc_ids=frozenset({"create-hook"}),
        eligible_doc_ids=frozenset({"database-shards", "context7"}),
    )
    config = skills_gate_config(
        workspace=fixture_pack.workspace,
        catalog_dir=fixture_pack.catalog_dir,
        skills_dir=fixture_pack.skills_dir,
        frontmatter_upper_limit=fixture_pack.frontmatter_upper_limit,
    )
    entries = build_registry_from_fixture_pack(fixture_pack)

    excluded = excluded_by_frontmatter_gate(scenario.query, entries, config=config)
    blocked_entry = next(entry for entry in entries if entry.doc_id == "create-hook")
    assert (blocked_entry.entry_dir, blocked_entry.doc_id) in excluded


def test_search_skills_finds_body_match_after_description_gate_blocks(
    fixture_pack: FrontmatterGateFixturePack,
) -> None:
    config = skills_gate_config(
        workspace=fixture_pack.workspace,
        catalog_dir=fixture_pack.catalog_dir,
        skills_dir=fixture_pack.skills_dir,
        frontmatter_upper_limit=fixture_pack.frontmatter_upper_limit,
    )
    entries = build_registry_from_fixture_pack(fixture_pack)
    query = "database shard rebalancing zebra migration"

    eligible = eligible_skills_after_gate(query, entries, config=config)
    assert _doc_ids(eligible) == {"create-hook", "context7"}

    matches = search_skills(query, eligible, config=config, skip_frontmatter_gate=True)
    match_doc_ids = {match.doc_id for match in matches}
    assert "create-hook" in match_doc_ids
    assert "database-shards" not in match_doc_ids
    create_hook = next(match for match in matches if match.doc_id == "create-hook")
    assert create_hook.score > 0.0


def test_search_skills_never_returns_gate_blocked_skill(
    fixture_pack: FrontmatterGateFixturePack,
) -> None:
    config = skills_gate_config(
        workspace=fixture_pack.workspace,
        catalog_dir=fixture_pack.catalog_dir,
        skills_dir=fixture_pack.skills_dir,
        frontmatter_upper_limit=fixture_pack.frontmatter_upper_limit,
    )
    entries = build_registry_from_fixture_pack(fixture_pack)
    query = "create-hook agent hooks for claude code"

    matches = search_skills(query, entries, config=config)
    match_doc_ids = {match.doc_id for match in matches}
    assert "create-hook" not in match_doc_ids


def test_client_skills_hook_payload_respects_frontmatter_gate(
    fixture_pack: FrontmatterGateFixturePack,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.conftest import isolate_user_home

    isolate_user_home(monkeypatch, fixture_pack.workspace / "home")
    config = skills_gate_config(
        workspace=fixture_pack.workspace,
        catalog_dir=fixture_pack.catalog_dir,
        skills_dir=fixture_pack.skills_dir,
        frontmatter_upper_limit=fixture_pack.frontmatter_upper_limit,
    )
    payload = client_payload_for_fixture_pack(fixture_pack)
    query = "use context7 specific library documentation"

    clear_registry_cache()
    entries = build_registry_for_hook_payload(config, payload)
    eligible = eligible_skills_after_gate(query, entries, config=config)

    assert _doc_ids(entries) == {"create-hook", "database-shards", "context7"}
    assert _doc_ids(eligible) == {"create-hook", "database-shards"}
