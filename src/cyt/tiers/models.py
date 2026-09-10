"""Tier manager data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cyt.config.policy_catalog import ToolPolicyRef


class Tier(IntEnum):
    """Canonical T0-T4 tier ordering (higher = hotter)."""

    DORMANT = 0
    COLD = 1
    ACTIVE = 2
    HOT = 3
    EXTRA_HOT = 4

    @classmethod
    def default(cls) -> Tier:
        return cls.ACTIVE


class EntityKind:
    TOOL = "tool"
    SKILL = "skill"


@dataclass(frozen=True)
class TierProject:
    project_id: int
    root_path: Path


@dataclass
class EffectiveStats:
    candidates: float = 0.0
    injected: float = 0.0
    used: float = 0.0
    used_without_injection: float = 0.0
    optional_used: float = 0.0
    shadow_hits: float = 0.0
    shadow_evaluations: float = 0.0
    last_seen_ms: int = 0
    requests_since_decay: int = 0

    def decay(self, *, half_life: float) -> None:
        if self.requests_since_decay <= 0:
            return
        factor = 2.0 ** (-self.requests_since_decay / half_life)
        self.candidates *= factor
        self.injected *= factor
        self.used *= factor
        self.used_without_injection *= factor
        self.optional_used *= factor
        self.shadow_hits *= factor
        self.shadow_evaluations *= factor
        self.requests_since_decay = 0


@dataclass
class EntityTierState:
    entity_id: str
    kind: str
    stable_tier: Tier = Tier.ACTIVE
    effective_tier: Tier = Tier.ACTIVE
    overlap_tier: Tier | None = None
    tier_since_epoch: int = 0
    temp_promotion_until_ms: int | None = None
    wake_lease_until_session: int = 0
    sleep_cooldown_until_session: int = 0
    pipeline: str = "default"
    stats: EffectiveStats = field(default_factory=EffectiveStats)


@dataclass(frozen=True)
class EntityTierView:
    entity_id: str
    tier: Tier
    stable_tier: Tier
    overlap_tier: Tier | None
    temporary: bool


@dataclass
class TierSnapshot:
    project: TierProject | None
    epoch_id: int
    epoch_start_ms: int
    session_id: int
    entities: dict[tuple[str, str], EntityTierView] = field(default_factory=dict)
    shadow_mode: bool = True
    enabled: bool = False

    def effective_tier(self, kind: str, entity_id: str) -> Tier:
        view = self.entities.get((kind, entity_id))
        if view is None:
            return Tier.ACTIVE
        return view.tier

    def tool_policy_name(self, tier: Tier) -> str | None:
        mapping = {
            Tier.COLD: "tier_cold",
            Tier.ACTIVE: "tier_active",
            Tier.HOT: "tier_hot",
            Tier.EXTRA_HOT: "always_include",
        }
        return mapping.get(tier)


@dataclass
class ToolsTierApplyResult:
    eligible_tools: list[dict[str, Any]]
    t4_direct: list[dict[str, Any]]
    excluded_t0: list[str]
    policy_overrides: dict[str, ToolPolicyRef]
    tier_by_tool: dict[str, Tier]


@dataclass
class SkillsTierPartition:
    search_entries: list[Any]
    t4_direct: list[Any]
    tier_by_skill: dict[str, Tier]
    representation_by_skill: dict[str, Tier]


@dataclass
class TierTransition:
    kind: str
    entity_id: str
    from_tier: Tier
    to_tier: Tier
    reason: str
    temporary: bool = False


@dataclass
class EpochState:
    epoch_id: int = 0
    epoch_start_ms: int = 0
    last_request_ms: int = 0
    session_id: int = 0
