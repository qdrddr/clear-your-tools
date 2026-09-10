"""Fast-clock T0↔T1 wake and sleep evaluator."""

from __future__ import annotations

import logging

from cyt.tiers.config import TierSectionConfig
from cyt.tiers.models import EntityTierState, Tier, TierTransition
from cyt.tiers.scores import shadow_score

logger = logging.getLogger(__name__)


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
    session_id: int,
    query_relevance: float = 0.0,
    sibling_activity: float = 0.0,
) -> TierTransition | None:
    if state.effective_tier != Tier.DORMANT:
        return None
    if session_id <= state.sleep_cooldown_until_session:
        return None
    pressure = wake_pressure(
        state,
        cfg=cfg,
        query_relevance=query_relevance,
        sibling_activity=sibling_activity,
    )
    if pressure < cfg.wake_threshold and state.stats.used_without_injection <= 0:
        return None
    old = state.effective_tier
    state.effective_tier = Tier.COLD
    state.stable_tier = Tier.COLD
    state.wake_lease_until_session = session_id + cfg.wake_lease_sessions
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
    session_id: int,
    had_selection: bool,
    had_use: bool,
    had_shadow: bool,
) -> TierTransition | None:
    if state.effective_tier != Tier.COLD:
        return None
    if session_id > state.wake_lease_until_session:
        pass
    elif state.wake_lease_until_session > 0:
        return None
    if had_use or had_selection or had_shadow or state.stats.used_without_injection > 0:
        if state.wake_lease_until_session > 0:
            state.wake_lease_until_session = session_id + cfg.wake_lease_sessions
        return None
    old = state.effective_tier
    state.effective_tier = Tier.DORMANT
    state.stable_tier = Tier.DORMANT
    state.sleep_cooldown_until_session = session_id + cfg.sleep_cooldown_sessions
    state.wake_lease_until_session = 0
    logger.debug("sleep %s:%s T1→T0", state.kind, state.entity_id)
    return TierTransition(
        kind=state.kind,
        entity_id=state.entity_id,
        from_tier=old,
        to_tier=Tier.DORMANT,
        reason="fast_sleep",
        temporary=False,
    )


def fast_promote_on_optional_use(state: EntityTierState) -> TierTransition | None:
    """Tools-only fast signal when optional properties were used."""
    if state.kind != "tool":
        return None
    tier = state.effective_tier
    if tier >= Tier.HOT:
        return None
    target = Tier.HOT if tier >= Tier.ACTIVE else Tier.ACTIVE
    old = state.effective_tier
    state.effective_tier = target
    if tier >= Tier.COLD:
        state.overlap_tier = state.stable_tier
    return TierTransition(
        kind=state.kind,
        entity_id=state.entity_id,
        from_tier=old,
        to_tier=target,
        reason="optional_property_used",
        temporary=True,
    )
