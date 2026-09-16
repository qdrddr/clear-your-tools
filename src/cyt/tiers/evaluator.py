"""Slow-clock epoch evaluator for T1-T4 transitions."""

from __future__ import annotations

import logging
import time

from cyt.tiers.config import TierSectionConfig
from cyt.tiers.models import EntityTierState, EpochState, Tier, TierTransition
from cyt.tiers.scores import demand_score, tool_t4_demand_met, utility_score

logger = logging.getLogger(__name__)


def epoch_ttl_ms(cfg: TierSectionConfig) -> int:
    ttl_ms = int(cfg.prompt_cache_ttl_minutes * 60 * 1000 * cfg.ttl_multiplier)
    if ttl_ms <= 0:
        ttl_ms = 5 * 60 * 1000
    return ttl_ms


def epoch_age_remaining_ms(
    *,
    now_ms: int,
    epoch: EpochState,
    cfg: TierSectionConfig,
) -> int:
    ttl_ms = epoch_ttl_ms(cfg)
    return max(0, ttl_ms - (now_ms - epoch.epoch_start_ms))


def epoch_idle_remaining_ms(
    *,
    now_ms: int,
    epoch: EpochState,
    cfg: TierSectionConfig,
) -> int:
    if not cfg.idle_gap_triggers_epoch:
        return epoch_ttl_ms(cfg)
    idle_limit_ms = int(cfg.prompt_cache_ttl_minutes * 60 * 1000)
    return max(0, idle_limit_ms - (now_ms - epoch.last_request_ms))


def epoch_remaining_ms(
    *,
    now_ms: int,
    epoch: EpochState,
    cfg: TierSectionConfig,
) -> int:
    return min(
        epoch_age_remaining_ms(now_ms=now_ms, epoch=epoch, cfg=cfg),
        epoch_idle_remaining_ms(now_ms=now_ms, epoch=epoch, cfg=cfg),
    )


def epoch_age_expired(
    *,
    now_ms: int,
    epoch: EpochState,
    cfg: TierSectionConfig,
) -> bool:
    return epoch_age_remaining_ms(now_ms=now_ms, epoch=epoch, cfg=cfg) <= 0


def epoch_boundary(
    *,
    now_ms: int,
    epoch: EpochState,
    cfg: TierSectionConfig,
) -> bool:
    return epoch_remaining_ms(now_ms=now_ms, epoch=epoch, cfg=cfg) <= 0


def _min_injections_met(state: EntityTierState, minimum: int) -> bool:
    return state.stats.injected >= minimum


def _min_exposure_met(state: EntityTierState, minimum: int) -> bool:
    """Enough BM25-eligible exposures to reconsider demotion without any injection."""
    return state.stats.candidates >= minimum


def _promote_tier(
    state: EntityTierState,
    target: Tier,
    *,
    reason: str,
    temporary: bool,
    cfg: TierSectionConfig | None = None,
) -> TierTransition:
    old = state.effective_tier
    if target > state.stable_tier:
        state.overlap_tier = state.stable_tier
    state.effective_tier = target
    if not temporary:
        state.stable_tier = target
        state.overlap_tier = None
    if temporary:
        from cyt.tiers.wake import temp_promotion_until_ms

        now_ms = int(time.time() * 1000)
        if cfg is not None:
            state.temp_promotion_until_ms = temp_promotion_until_ms(cfg, now_ms=now_ms)
        else:
            state.temp_promotion_until_ms = now_ms + 3 * 60 * 1000
    return TierTransition(
        kind=state.kind,
        entity_id=state.entity_id,
        from_tier=old,
        to_tier=target,
        reason=reason,
        temporary=temporary,
    )


def _demote_tier(state: EntityTierState, target: Tier, *, reason: str) -> TierTransition:
    old = state.effective_tier
    state.effective_tier = target
    state.stable_tier = target
    state.overlap_tier = None
    state.temp_promotion_until_ms = None
    return TierTransition(
        kind=state.kind,
        entity_id=state.entity_id,
        from_tier=old,
        to_tier=target,
        reason=reason,
        temporary=False,
    )


def _epoch_had_success(state: EntityTierState) -> bool:
    return state.stats.epoch_used > 0


def _tool_skip_demotion_on_epoch_success(state: EntityTierState) -> bool:
    return state.kind == "tool" and _epoch_had_success(state)


def _t4_promotion_demand_met(state: EntityTierState, *, cfg: TierSectionConfig) -> bool:
    threshold = cfg.thresholds_t34.promote_demand
    if state.kind == "tool":
        return tool_t4_demand_met(
            state.stats,
            demand_threshold=threshold,
            min_used=float(cfg.min_injections_before_reconsider * 2),
        )
    return demand_score(state.stats) >= threshold


def _t4_promotion_criteria_met(state: EntityTierState, *, cfg: TierSectionConfig) -> bool:
    min_inj = cfg.min_injections_before_reconsider
    if not _min_injections_met(state, min_inj):
        return False
    if state.stats.injected < min_inj * 2:
        return False
    if utility_score(state.stats) < cfg.thresholds_t34.promote_utility:
        return False
    return _t4_promotion_demand_met(state, cfg=cfg)


def evaluate_slow_clock(  # noqa: C901
    states: dict[tuple[str, str], EntityTierState],
    *,
    cfg: TierSectionConfig,
    epoch: EpochState,
) -> list[TierTransition]:
    transitions: list[TierTransition] = []
    for state in states.values():
        if state.effective_tier <= Tier.DORMANT:
            continue
        skip_demotion = _tool_skip_demotion_on_epoch_success(state)
        d = demand_score(state.stats)
        u = utility_score(state.stats)
        tier = state.stable_tier

        if tier == Tier.EXTRA_HOT:
            if not skip_demotion:
                if (
                    state.stats.injected >= cfg.emergency_t4_inject_min
                    and u < cfg.emergency_t4_utility_max
                ):
                    transitions.append(
                        _demote_tier(state, Tier.HOT, reason="emergency_t4_eviction"),
                    )
                elif _min_injections_met(state, cfg.min_injections_before_reconsider) and (
                    d < cfg.thresholds_t34.demote_demand or u < cfg.thresholds_t34.demote_utility
                ):
                    transitions.append(_demote_tier(state, Tier.HOT, reason="slow_demote_t4_t3"))
            continue

        if tier == Tier.HOT:
            if _t4_promotion_criteria_met(state, cfg=cfg):
                transitions.append(
                    _promote_tier(
                        state,
                        Tier.EXTRA_HOT,
                        reason="slow_promote_t3_t4",
                        temporary=False,
                    ),
                )
            elif (
                not skip_demotion
                and _min_injections_met(
                    state,
                    cfg.min_injections_before_reconsider,
                )
                and (d < cfg.thresholds_t23.demote_demand or u < cfg.thresholds_t23.demote_utility)
            ):
                transitions.append(_demote_tier(state, Tier.ACTIVE, reason="slow_demote_t3_t2"))
            continue

        if tier == Tier.ACTIVE:
            if skip_demotion:
                continue
            if (
                _min_injections_met(state, cfg.min_injections_before_reconsider)
                and d >= cfg.thresholds_t23.promote_demand
                and (
                    u >= cfg.thresholds_t23.promote_utility
                    or state.stats.used_without_injection > 0
                )
            ):
                transitions.append(
                    _promote_tier(state, Tier.HOT, reason="slow_promote_t2_t3", temporary=False),
                )
            elif (
                _min_exposure_met(state, cfg.min_injections_before_reconsider)
                and d < cfg.thresholds_t12.demote_demand
                and u < cfg.thresholds_t12.demote_utility
            ):
                transitions.append(_demote_tier(state, Tier.COLD, reason="slow_demote_t2_t1"))
            continue

        if tier == Tier.COLD:
            if skip_demotion:
                continue
            if (
                _min_injections_met(state, cfg.min_injections_before_reconsider)
                and d >= cfg.thresholds_t12.promote_demand
                and (
                    u >= cfg.thresholds_t12.promote_utility
                    or state.stats.used_without_injection > 0
                )
            ):
                transitions.append(
                    _promote_tier(state, Tier.ACTIVE, reason="slow_promote_t1_t2", temporary=False),
                )

    if transitions:
        logger.debug("slow-clock transitions at epoch %s: %d", epoch.epoch_id, len(transitions))
    return transitions


def crystallize_successful_tool_promotions_at_epoch(
    states: dict[tuple[str, str], EntityTierState],
    *,
    epoch: EpochState,
) -> list[TierTransition]:
    """Promote tools with successful use this epoch to stable HOT (recent signal wins)."""
    transitions: list[TierTransition] = []
    for state in states.values():
        if state.kind != "tool" or not _epoch_had_success(state):
            continue
        state.temp_promotion_until_ms = None
        state.overlap_tier = None
        if state.stable_tier >= Tier.HOT:
            state.effective_tier = state.stable_tier
            continue
        target = max(state.effective_tier, Tier.HOT)
        old_stable = state.stable_tier
        state.stable_tier = target
        state.effective_tier = target
        transitions.append(
            TierTransition(
                kind=state.kind,
                entity_id=state.entity_id,
                from_tier=old_stable,
                to_tier=target,
                reason="slow_promote_epoch_crystallized",
                temporary=False,
            ),
        )
    return transitions


def expire_temporary_promotions(
    states: dict[tuple[str, str], EntityTierState],
    *,
    now_ms: int,
) -> list[TierTransition]:
    transitions: list[TierTransition] = []
    for state in states.values():
        until = state.temp_promotion_until_ms
        if until is None or now_ms < until:
            continue
        if state.effective_tier != state.stable_tier:
            old = state.effective_tier
            crystallize = state.kind == "tool" and old > state.stable_tier and state.stats.used > 0
            if crystallize:
                old_stable = state.stable_tier
                state.stable_tier = old
                state.effective_tier = old
                state.overlap_tier = None
                state.temp_promotion_until_ms = None
                transitions.append(
                    TierTransition(
                        kind=state.kind,
                        entity_id=state.entity_id,
                        from_tier=old_stable,
                        to_tier=old,
                        reason="slow_promote_temp_crystallized",
                        temporary=False,
                    ),
                )
                continue
            state.effective_tier = state.stable_tier
            state.overlap_tier = None
            state.temp_promotion_until_ms = None
            transitions.append(
                TierTransition(
                    kind=state.kind,
                    entity_id=state.entity_id,
                    from_tier=old,
                    to_tier=state.stable_tier,
                    reason="temp_promotion_expired",
                    temporary=True,
                ),
            )
    return transitions
