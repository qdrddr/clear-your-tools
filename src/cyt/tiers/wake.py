"""Fast-clock T0↔T1 wake and sleep evaluator."""

from __future__ import annotations

import logging
import time

from cyt.tiers.config import TierSectionConfig
from cyt.tiers.models import EntityTierState, Tier, TierTransition
from cyt.tiers.scores import shadow_score

logger = logging.getLogger(__name__)


def temp_promotion_until_ms(cfg: TierSectionConfig, *, now_ms: int | None = None) -> int:
    """Wall-clock expiry for fast T→HOT promotions (turns x prompt-cache TTL)."""
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    turns = max(int(cfg.temp_promotion_turns), 1)
    minutes = cfg.prompt_cache_ttl_minutes * cfg.ttl_multiplier * turns
    return now_ms + int(max(minutes, 1.0) * 60 * 1000)


def wake_pressure(
    state: EntityTierState,
    *,
    cfg: TierSectionConfig,
    query_relevance: float = 0.0,
    sibling_activity: float = 0.0,
) -> float:
    if state.stats.used_without_injection > 0:
        return 1.0
    weights = cfg.wake_weights
    s = shadow_score(state.stats)
    direct = 1.0 if state.stats.used_without_injection > 0 else 0.0
    return (
        weights.get("shadow", 0.4) * s
        + weights.get("direct", 0.3) * direct
        + weights.get("query", 0.2) * query_relevance
        + weights.get("sibling", 0.1) * sibling_activity
    )


def evaluate_fast_wake(
    state: EntityTierState,
    *,
    cfg: TierSectionConfig,
    wake_cycle_id: int,
    query_relevance: float = 0.0,
    sibling_activity: float = 0.0,
) -> TierTransition | None:
    if state.effective_tier != Tier.DORMANT:
        return None
    if wake_cycle_id <= state.sleep_cooldown_until_cycle:
        return None
    pressure = wake_pressure(
        state,
        cfg=cfg,
        query_relevance=query_relevance,
        sibling_activity=sibling_activity,
    )
    if (
        pressure < cfg.wake_threshold
        and state.stats.used_without_injection <= 0
        and query_relevance < cfg.wake_threshold
    ):
        return None
    old = state.effective_tier
    state.effective_tier = Tier.COLD
    state.stable_tier = Tier.COLD
    state.wake_lease_until_cycle = wake_cycle_id + cfg.wake_lease_cycles
    logger.debug("wake %s:%s T0→T1 pressure=%.3f", state.kind, state.entity_id, pressure)
    return TierTransition(
        kind=state.kind,
        entity_id=state.entity_id,
        from_tier=old,
        to_tier=Tier.COLD,
        reason="fast_wake",
        temporary=False,
    )


def evaluate_fast_sleep(
    state: EntityTierState,
    *,
    cfg: TierSectionConfig,
    wake_cycle_id: int,
    had_selection: bool,
    had_use: bool,
    had_shadow: bool,
) -> TierTransition | None:
    if state.effective_tier != Tier.COLD:
        return None
    if wake_cycle_id > state.wake_lease_until_cycle:
        pass
    elif state.wake_lease_until_cycle > 0:
        return None
    if had_use or had_selection or had_shadow or state.stats.used_without_injection > 0:
        if state.wake_lease_until_cycle > 0:
            state.wake_lease_until_cycle = wake_cycle_id + cfg.wake_lease_cycles
        return None
    old = state.effective_tier
    state.effective_tier = Tier.DORMANT
    state.stable_tier = Tier.DORMANT
    state.sleep_cooldown_until_cycle = wake_cycle_id + cfg.sleep_cooldown_cycles
    state.wake_lease_until_cycle = 0
    logger.debug("sleep %s:%s T1→T0", state.kind, state.entity_id)
    return TierTransition(
        kind=state.kind,
        entity_id=state.entity_id,
        from_tier=old,
        to_tier=Tier.DORMANT,
        reason="fast_sleep",
        temporary=False,
    )


def fast_promote_on_tool_use(
    state: EntityTierState,
    *,
    cfg: TierSectionConfig | None = None,
) -> TierTransition | None:
    """Fast signal when the agent invoked a tool or skill."""
    if state.kind not in ("tool", "skill"):
        return None
    now_ms = int(time.time() * 1000)
    if state.stable_tier >= Tier.HOT:
        state.temp_promotion_until_ms = None
        state.overlap_tier = None
        if state.effective_tier < state.stable_tier:
            state.effective_tier = state.stable_tier
        return None
    tier = state.effective_tier
    if tier >= Tier.HOT:
        return None
    # Successful invocation is a strong signal: bump to temp HOT from T1/T2 (or T0→T2).
    target = Tier.HOT if tier >= Tier.COLD else Tier.ACTIVE
    old = state.effective_tier
    state.effective_tier = target
    if tier >= Tier.COLD:
        state.overlap_tier = state.stable_tier
    if cfg is not None:
        state.temp_promotion_until_ms = temp_promotion_until_ms(cfg, now_ms=now_ms)
    else:
        state.temp_promotion_until_ms = now_ms + 3 * 60 * 1000
    return TierTransition(
        kind=state.kind,
        entity_id=state.entity_id,
        from_tier=old,
        to_tier=target,
        reason="skill_used" if state.kind == "skill" else "tool_used",
        temporary=True,
    )


def fast_promote_on_optional_use(
    state: EntityTierState,
    *,
    cfg: TierSectionConfig | None = None,
) -> TierTransition | None:
    """Tools-only fast signal when optional properties were used."""
    transition = fast_promote_on_tool_use(state, cfg=cfg)
    if transition is not None:
        transition = TierTransition(
            kind=transition.kind,
            entity_id=transition.entity_id,
            from_tier=transition.from_tier,
            to_tier=transition.to_tier,
            reason="optional_property_used",
            temporary=True,
        )
    return transition
