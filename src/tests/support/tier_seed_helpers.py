"""Shared tier DB seeding for tests (BM25 tier goldens, runtime E2E, fixture packs)."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from cyt.tiers.models import EffectiveStats, EntityTierState, Tier, TierProject
from cyt.tiers.store import TierStore

TIER_BY_NAME: dict[str, Tier] = {
    "DORMANT": Tier.DORMANT,
    "COLD": Tier.COLD,
    "ACTIVE": Tier.ACTIVE,
    "HOT": Tier.HOT,
    "EXTRA_HOT": Tier.EXTRA_HOT,
}


def parse_tier(raw: object, *, default: Tier = Tier.ACTIVE) -> Tier:
    if isinstance(raw, Tier):
        return raw
    if isinstance(raw, str):
        return TIER_BY_NAME.get(raw.strip().upper(), default)
    return default


def seed_entity_tier(
    *,
    workspace: Path,
    db_path: Path,
    kind: str,
    entity_id: str,
    tier: Tier,
) -> None:
    store = TierStore.open(str(db_path))
    try:
        project_id = store.get_or_create_project(str(workspace))
        project = TierProject(project_id=project_id, root_path=workspace)
        store.upsert_entity_state(
            project,
            EntityTierState(
                entity_id=entity_id,
                kind=kind,
                stable_tier=tier,
                effective_tier=tier,
                stats=EffectiveStats(),
            ),
        )
    finally:
        store.close()


def seed_tool_tiers(
    *,
    workspace: Path,
    db_path: Path,
    tier_map: Mapping[str, Tier | str],
) -> None:
    for entity_id, tier in tier_map.items():
        seed_entity_tier(
            workspace=workspace,
            db_path=db_path,
            kind="tool",
            entity_id=entity_id,
            tier=parse_tier(tier),
        )


def seed_skill_tiers(
    *,
    workspace: Path,
    db_path: Path,
    tier_map: Mapping[str, Tier | str],
) -> None:
    for entity_id, tier in tier_map.items():
        seed_entity_tier(
            workspace=workspace,
            db_path=db_path,
            kind="skill",
            entity_id=entity_id,
            tier=parse_tier(tier),
        )


def seed_tiers_from_mapping(
    *,
    workspace: Path,
    db_path: Path,
    tool_tiers: Mapping[str, str] | None = None,
    skill_tiers: Mapping[str, str] | None = None,
) -> None:
    if tool_tiers:
        seed_tool_tiers(workspace=workspace, db_path=db_path, tier_map=tool_tiers)
    if skill_tiers:
        seed_skill_tiers(workspace=workspace, db_path=db_path, tier_map=skill_tiers)


__all__ = [
    "TIER_BY_NAME",
    "parse_tier",
    "seed_entity_tier",
    "seed_skill_tiers",
    "seed_tiers_from_mapping",
    "seed_tool_tiers",
]
