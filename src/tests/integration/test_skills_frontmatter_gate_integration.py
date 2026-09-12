"""Integration tests: frontmatter gate before skills pruning pipeline."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from cyt.pruning.coordinator import coordinate_skills_tools_prune
from cyt.pruning.hook_bridge import run_hook_coordinated_prune
from cyt.skills.catalog import SkillEntryRef
from cyt.skills.proxy_inject import prepare_deferred_skills_context, resolve_skills_for_query
from cyt.skills.search import eligible_skills_after_gate
from tests.support.skills_frontmatter_gate_fixtures import (
    FrontmatterGateFixturePack,
    build_registry_from_fixture_pack,
    client_payload_for_fixture_pack,
    load_scenarios,
    materialize_fixture_pack,
    skills_gate_config,
)


def _doc_ids(entries: Sequence[SkillEntryRef]) -> set[str]:
    return {entry.doc_id for entry in entries}


@pytest.fixture
def fixture_pack(tmp_path: Path) -> FrontmatterGateFixturePack:
    return materialize_fixture_pack(tmp_path)


def test_hook_coordinated_prune_skips_gate_blocked_skills(
    fixture_pack: FrontmatterGateFixturePack,
) -> None:
    config = skills_gate_config(
        workspace=fixture_pack.workspace,
        catalog_dir=fixture_pack.catalog_dir,
        skills_dir=fixture_pack.skills_dir,
        frontmatter_upper_limit=fixture_pack.frontmatter_upper_limit,
    )
    query = "create-hook agent hooks for claude code"
    entries = build_registry_from_fixture_pack(fixture_pack)
    gated = eligible_skills_after_gate(query, entries, config=config)
    assert _doc_ids(gated) == {"database-shards", "context7"}

    received_entry_ids: list[set[str]] = []

    def capture_skills_search(
        query: str,
        config: dict[str, Any],
        *,
        max_tokens: int | None = None,
        upstream_kind: str | None = None,
        pruner_settings: object = None,
        entries: list[Any] | None = None,
        skip_frontmatter_gate: bool = False,
    ) -> list[Any]:
        del query, config, max_tokens, upstream_kind, pruner_settings, skip_frontmatter_gate
        received_entry_ids.append(_doc_ids(entries or []))
        return []

    with patch(
        "cyt.skills.proxy_inject.resolve_skills_for_query",
        side_effect=capture_skills_search,
    ):
        coordinated = coordinate_skills_tools_prune(
            query,
            config,
            tool_sources=[],
            skill_entries=gated,
            for_hook=True,
            skills_allowed=True,
            tools_allowed=False,
        )

    assert received_entry_ids == [{"database-shards", "context7"}]
    assert coordinated.skill_matches == []


def test_run_hook_coordinated_prune_applies_gate_before_search(
    fixture_pack: FrontmatterGateFixturePack,
) -> None:
    config = skills_gate_config(
        workspace=fixture_pack.workspace,
        catalog_dir=fixture_pack.catalog_dir,
        skills_dir=fixture_pack.skills_dir,
        frontmatter_upper_limit=fixture_pack.frontmatter_upper_limit,
    )
    payload = client_payload_for_fixture_pack(fixture_pack)
    query = "use context7 specific library documentation"

    searched_doc_ids: list[set[str]] = []

    def capture_skills_search(
        query: str,
        config: dict[str, Any],
        *,
        max_tokens: int | None = None,
        upstream_kind: str | None = None,
        pruner_settings: object = None,
        entries: list[Any] | None = None,
        skip_frontmatter_gate: bool = False,
    ) -> list[Any]:
        del query, config, max_tokens, upstream_kind, pruner_settings, skip_frontmatter_gate
        searched_doc_ids.append(_doc_ids(entries or []))
        return []

    with patch(
        "cyt.skills.proxy_inject.resolve_skills_for_query",
        side_effect=capture_skills_search,
    ):
        _prune_result, skill_matches, _catalog, _by_source, _timing = run_hook_coordinated_prune(
            query,
            config,
            payload=payload,
            skills_allowed=True,
            tools_allowed=False,
        )

    assert searched_doc_ids == [{"create-hook", "database-shards"}]
    assert skill_matches == []


@pytest.mark.parametrize(
    "scenario_id",
    [scenario.id for scenario in load_scenarios()[1]],
    ids=[scenario.id for scenario in load_scenarios()[1]],
)
def test_proxy_deferred_context_only_includes_gate_eligible_skills(
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

    with patch("cyt.skills.proxy_inject.build_registry", return_value=entries):
        deferred = prepare_deferred_skills_context(
            config,
            scenario.query,
            kind="anthropic",
            body={"model": "claude", "messages": [{"role": "user", "content": scenario.query}]},
        )

    assert deferred is not None
    assert deferred.skills_allowed is True
    assert _doc_ids(deferred.skill_entries) == scenario.eligible_doc_ids


def test_resolve_skills_for_query_does_not_prune_gate_blocked_entries(
    fixture_pack: FrontmatterGateFixturePack,
) -> None:
    config = skills_gate_config(
        workspace=fixture_pack.workspace,
        catalog_dir=fixture_pack.catalog_dir,
        skills_dir=fixture_pack.skills_dir,
        frontmatter_upper_limit=fixture_pack.frontmatter_upper_limit,
    )
    query = "create-hook agent hooks for claude code"
    entries = build_registry_from_fixture_pack(fixture_pack)

    matches = resolve_skills_for_query(
        query,
        config,
        entries=entries,
        upstream_kind="anthropic",
    )
    match_doc_ids = {match.doc_id for match in matches}
    assert "create-hook" not in match_doc_ids
