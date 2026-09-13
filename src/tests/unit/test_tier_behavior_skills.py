"""Unit tests for live skill tier behavior using shared fixture files."""

from __future__ import annotations

from pathlib import Path

import pytest

from cyt.skills.catalog import SkillEntryRef
from cyt.skills.search import MatchedSkill
from cyt.tiers.adapters.skills import (
    apply_skill_representation,
    partition_skill_entries,
    skill_entity_id,
)
from cyt.tiers.models import Tier
from cyt.tiers.skill_token_materialization import (
    clear_carried_token_memo,
    effective_skill_token_count,
    markdown_for_tier,
)
from tests.support.tier_behavior_fixtures import (
    SkillTierExpectation,
    TierBehaviorFixturePack,
    build_registry_from_pack,
    iter_skill_tier_cases,
    load_skill_scenarios,
    materialize_fixture_pack,
    skill_fixture_key,
)


@pytest.fixture
def fixture_pack(tmp_path: Path) -> TierBehaviorFixturePack:
    return materialize_fixture_pack(tmp_path)


def _entry_for_doc_id(pack: TierBehaviorFixturePack, doc_id: str) -> SkillEntryRef:
    for entry in build_registry_from_pack(pack):
        if skill_fixture_key(entry) == doc_id:
            return entry
    raise KeyError(f"unknown skill fixture key: {doc_id}")


def _matched_skill_for_doc_id(pack: TierBehaviorFixturePack, doc_id: str) -> MatchedSkill:
    path = pack.skill_paths_by_doc_id[doc_id]
    markdown = path.read_text(encoding="utf-8")
    return MatchedSkill(
        doc_id=doc_id,
        file_path=str(path),
        markdown=markdown,
        name=doc_id,
        score=1.0,
        token_count=0,
    )


@pytest.mark.parametrize(
    ("doc_id", "expectation"),
    [(doc_id, expectation) for doc_id, expectation in iter_skill_tier_cases()],
    ids=[f"{doc_id}-{expectation.raw['tier']}" for doc_id, expectation in iter_skill_tier_cases()],
)
def test_partition_skill_entries_matches_fixture(
    fixture_pack: TierBehaviorFixturePack,
    doc_id: str,
    expectation: SkillTierExpectation,
) -> None:
    entries = build_registry_from_pack(fixture_pack)
    entry = _entry_for_doc_id(fixture_pack, doc_id)
    entity_id = skill_entity_id(entry)
    tier_map = {entity_id: expectation.tier}
    partition = partition_skill_entries(entries, tier_for_skill=tier_map, apply=True)

    search_ids = {skill_fixture_key(item) for item in partition.search_entries}
    t4_ids = {skill_fixture_key(item) for item in partition.t4_direct}
    raw = expectation.raw

    if raw.get("in_search_pool"):
        assert doc_id in search_ids
    else:
        assert doc_id not in search_ids

    if raw.get("in_t4_direct"):
        assert doc_id in t4_ids
    else:
        assert doc_id not in t4_ids


@pytest.mark.parametrize(
    ("doc_id", "expectation"),
    [(doc_id, expectation) for doc_id, expectation in iter_skill_tier_cases()],
    ids=[
        f"{doc_id}-repr-{expectation.raw['tier']}"
        for doc_id, expectation in iter_skill_tier_cases()
    ],
)
def test_apply_skill_representation_matches_fixture(
    fixture_pack: TierBehaviorFixturePack,
    doc_id: str,
    expectation: SkillTierExpectation,
) -> None:
    match = _matched_skill_for_doc_id(fixture_pack, doc_id)
    original = match.markdown
    shaped = apply_skill_representation(match, expectation.tier)
    tier_label = f"T{expectation.tier.value}"
    trimmed = markdown_for_tier(original, tier_label, entity={"source_path": match.file_path})
    raw = expectation.raw

    if raw.get("representation") == "unchanged":
        assert shaped.markdown == original
        return

    for fragment in raw.get("markdown_includes", []):
        assert fragment in shaped.markdown
        assert fragment in trimmed

    for fragment in raw.get("markdown_excludes", []):
        assert fragment not in shaped.markdown
        assert fragment not in trimmed


@pytest.mark.parametrize(
    ("doc_id", "expectation"),
    [(doc_id, expectation) for doc_id, expectation in iter_skill_tier_cases()],
    ids=[
        f"{doc_id}-tokens-{expectation.raw['tier']}"
        for doc_id, expectation in iter_skill_tier_cases()
    ],
)
def test_effective_skill_token_count_matches_fixture(
    fixture_pack: TierBehaviorFixturePack,
    doc_id: str,
    expectation: SkillTierExpectation,
) -> None:
    clear_carried_token_memo()
    path = fixture_pack.skill_paths_by_doc_id[doc_id]
    entity = {
        "entity_id": str(path),
        "source_path": str(path),
        "doc_id": doc_id,
        "name": doc_id,
    }
    tier_label = f"T{expectation.tier.value}"
    count = effective_skill_token_count(entity, tier_label)

    if "effective_tokens" in expectation.raw:
        assert count == expectation.raw["effective_tokens"]
    elif expectation.tier == Tier.DORMANT:
        assert count == 0


def test_skill_scenarios_cover_all_fixture_skills(fixture_pack: TierBehaviorFixturePack) -> None:
    doc_ids = set(fixture_pack.skill_paths_by_doc_id)
    scenario_ids = {scenario.doc_id for scenario in load_skill_scenarios()}
    assert doc_ids == scenario_ids


def test_skill_token_counts_are_monotonic_across_tiers(
    fixture_pack: TierBehaviorFixturePack,
) -> None:
    clear_carried_token_memo()
    doc_id = "create-hook"
    path = fixture_pack.skill_paths_by_doc_id[doc_id]
    entity = {
        "entity_id": str(path),
        "source_path": str(path),
        "doc_id": doc_id,
        "name": doc_id,
    }
    counts = [
        effective_skill_token_count(entity, f"T{tier.value}")
        for tier in (Tier.COLD, Tier.ACTIVE, Tier.HOT, Tier.EXTRA_HOT)
    ]
    resolved: list[int] = []
    for count in counts:
        assert count is not None
        resolved.append(count)
    assert resolved[0] <= resolved[1] <= resolved[2]
    assert resolved[2] == resolved[3]
