"""Detailed tier status serialization for troubleshooting."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cyt.tiers.entity_origin import (
    resolve_skill_origin_fields,
    resolve_tool_origin_fields,
)
from cyt.tiers.adapters.skills import (
    is_ephemeral_skill_path,
    resolve_skill_doc_id,
    resolve_skill_entity_id,
    resolve_skill_frontmatter_name,
    resolve_skill_source_path,
    skill_display_name,
    skill_doc_id_from_entity_id,
    skill_entity_visible_for_agent,
    tier_entity_id_for_skill,
)
from cyt.tiers.config import TierSectionConfig, TierThresholds
from cyt.tiers.models import EffectiveStats, EntityKind, EntityTierState, Tier
from cyt.tiers.scores import demand_score, shadow_score, utility_score
from cyt.tiers.wake import wake_pressure

_TIER_LABELS = tuple(f"T{i}" for i in range(5))


def tier_label(tier: Tier) -> str:
    return f"T{int(tier)}"


def effective_tier_for(state: EntityTierState, *, now_ms: int | None = None) -> Tier:
    """Match TierManager._effective_tier_for runtime tier computation."""
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    if state.temp_promotion_until_ms is not None and now_ms < state.temp_promotion_until_ms:
        return max(state.effective_tier, state.stable_tier)
    if state.overlap_tier is not None:
        return max(state.effective_tier, state.overlap_tier)
    return state.effective_tier


def _policy_for_tier(tier: Tier) -> str | None:
    mapping = {
        Tier.COLD: "tier_cold",
        Tier.ACTIVE: "tier_active",
        Tier.HOT: "tier_hot",
        Tier.EXTRA_HOT: "always_include",
    }
    return mapping.get(tier)


def _thresholds_dict(thresholds: TierThresholds) -> dict[str, float]:
    return {
        "promote_demand": thresholds.promote_demand,
        "demote_demand": thresholds.demote_demand,
        "promote_utility": thresholds.promote_utility,
        "demote_utility": thresholds.demote_utility,
    }


def config_summary(cfg: TierSectionConfig) -> dict[str, Any]:
    return {
        "request_half_life": cfg.request_half_life,
        "prompt_cache_ttl_minutes": cfg.prompt_cache_ttl_minutes,
        "ttl_multiplier": cfg.ttl_multiplier,
        "idle_gap_triggers_epoch": cfg.idle_gap_triggers_epoch,
        "min_injections_before_reconsider": cfg.min_injections_before_reconsider,
        "wake_threshold": cfg.wake_threshold,
        "wake_lease_sessions": cfg.wake_lease_sessions,
        "sleep_cooldown_sessions": cfg.sleep_cooldown_sessions,
        "emergency_t4_inject_min": cfg.emergency_t4_inject_min,
        "emergency_t4_utility_max": cfg.emergency_t4_utility_max,
        "temp_promotion_turns": cfg.temp_promotion_turns,
        "wake_weights": dict(cfg.wake_weights),
        "thresholds": {
            "t12": _thresholds_dict(cfg.thresholds_t12),
            "t23": _thresholds_dict(cfg.thresholds_t23),
            "t34": _thresholds_dict(cfg.thresholds_t34),
        },
    }


def _stats_dict(stats: EffectiveStats) -> dict[str, float | int]:
    return {
        "candidates": stats.candidates,
        "injected": stats.injected,
        "used": stats.used,
        "used_without_injection": stats.used_without_injection,
        "optional_used": stats.optional_used,
        "shadow_hits": stats.shadow_hits,
        "shadow_evaluations": stats.shadow_evaluations,
        "last_seen_ms": stats.last_seen_ms,
        "requests_since_decay": stats.requests_since_decay,
    }


def _min_injections_met(state: EntityTierState, minimum: int) -> bool:
    return state.stats.injected >= minimum


def _transient_placement_hints(
    state: EntityTierState,
    *,
    cfg: TierSectionConfig,
    session_id: int,
    now_ms: int,
    effective: Tier,
) -> list[str]:
    hints: list[str] = []
    base = state.stable_tier

    if state.temp_promotion_until_ms is not None and now_ms < state.temp_promotion_until_ms:
        hints.append("temp_promotion_active")
    elif effective > base:
        hints.append("temporary_effective_tier")

    if state.overlap_tier is not None:
        hints.append("overlap_tier_active")

    if state.wake_lease_until_session > session_id:
        hints.append("wake_lease_active")

    if session_id <= state.sleep_cooldown_until_session:
        hints.append("sleep_cooldown_active")

    return hints


def _dormant_placement_hints(
    state: EntityTierState,
    *,
    cfg: TierSectionConfig,
) -> list[str]:
    hints: list[str] = []
    pressure = wake_pressure(state, cfg=cfg)
    if pressure >= cfg.wake_threshold or state.stats.used_without_injection > 0:
        hints.append("wake_candidate")
    return hints


def _extra_hot_progression_hints(
    state: EntityTierState,
    *,
    cfg: TierSectionConfig,
    demand: float,
    utility: float,
) -> list[str]:
    if (
        state.stats.injected >= cfg.emergency_t4_inject_min
        and utility < cfg.emergency_t4_utility_max
    ):
        return ["emergency_t4_eviction_risk"]
    if demand < cfg.thresholds_t34.demote_demand or utility < cfg.thresholds_t34.demote_utility:
        return ["demote_risk_t4_t3"]
    return []


def _hot_progression_hints(
    state: EntityTierState,
    *,
    cfg: TierSectionConfig,
    demand: float,
    utility: float,
    min_inj: int,
) -> list[str]:
    if (
        demand >= cfg.thresholds_t34.promote_demand
        and utility >= cfg.thresholds_t34.promote_utility
        and state.stats.injected >= min_inj * 2
    ):
        return ["promote_candidate_t3_t4"]
    if demand < cfg.thresholds_t23.demote_demand or utility < cfg.thresholds_t23.demote_utility:
        return ["demote_risk_t3_t2"]
    return []


def _active_progression_hints(
    state: EntityTierState,
    *,
    cfg: TierSectionConfig,
    demand: float,
    utility: float,
) -> list[str]:
    if demand >= cfg.thresholds_t23.promote_demand and (
        utility >= cfg.thresholds_t23.promote_utility or state.stats.used_without_injection > 0
    ):
        return ["promote_candidate_t2_t3"]
    if demand < cfg.thresholds_t12.demote_demand and utility < cfg.thresholds_t12.demote_utility:
        return ["demote_risk_t2_t1"]
    return []


def _cold_progression_hints(
    state: EntityTierState,
    *,
    cfg: TierSectionConfig,
    demand: float,
    utility: float,
) -> list[str]:
    if demand >= cfg.thresholds_t12.promote_demand and (
        utility >= cfg.thresholds_t12.promote_utility or state.stats.used_without_injection > 0
    ):
        return ["promote_candidate_t1_t2"]
    return []


def _stable_tier_progression_hints(
    state: EntityTierState,
    *,
    cfg: TierSectionConfig,
    demand: float,
    utility: float,
    min_inj: int,
) -> list[str]:
    base = state.stable_tier
    if base == Tier.EXTRA_HOT:
        return _extra_hot_progression_hints(state, cfg=cfg, demand=demand, utility=utility)
    if base == Tier.HOT:
        return _hot_progression_hints(
            state,
            cfg=cfg,
            demand=demand,
            utility=utility,
            min_inj=min_inj,
        )
    if base == Tier.ACTIVE:
        return _active_progression_hints(state, cfg=cfg, demand=demand, utility=utility)
    if base == Tier.COLD:
        return _cold_progression_hints(state, cfg=cfg, demand=demand, utility=utility)
    return []


def _placement_hints(
    state: EntityTierState,
    *,
    cfg: TierSectionConfig,
    session_id: int,
    now_ms: int,
    effective: Tier,
) -> list[str]:
    hints = _transient_placement_hints(
        state,
        cfg=cfg,
        session_id=session_id,
        now_ms=now_ms,
        effective=effective,
    )
    base = state.stable_tier
    min_inj = cfg.min_injections_before_reconsider

    if base == Tier.DORMANT:
        hints.extend(_dormant_placement_hints(state, cfg=cfg))
        return hints

    if not _min_injections_met(state, min_inj):
        hints.append("below_min_injections")
        return hints

    hints.append("min_injections_met")
    hints.extend(
        _stable_tier_progression_hints(
            state,
            cfg=cfg,
            demand=demand_score(state.stats),
            utility=utility_score(state.stats),
            min_inj=min_inj,
        ),
    )
    return hints


def _skill_status_fields(
    state: EntityTierState,
    *,
    config: dict[str, Any] | None,
    workspace_root: Path | None = None,
) -> dict[str, Any]:
    entity_id = state.entity_id
    doc_id = resolve_skill_doc_id(entity_id)
    canonical = resolve_skill_entity_id(entity_id, doc_id=doc_id) or entity_id
    source_path = None
    if config is not None:
        source_path = resolve_skill_source_path(
            canonical,
            config,
            workspace_root=workspace_root,
        )
    if source_path is None and not is_ephemeral_skill_path(entity_id):
        source_path = entity_id
    fields: dict[str, Any] = {}
    if doc_id:
        fields["doc_id"] = doc_id
    if source_path:
        fields["source_path"] = source_path
    name = resolve_skill_frontmatter_name(
        canonical,
        config,
        source_path=source_path,
    )
    if name:
        fields["name"] = name
    display_name = skill_display_name(
        canonical,
        config=config,
        doc_id=doc_id,
        source_path=source_path,
        frontmatter_name=name,
    )
    if display_name:
        fields["display_name"] = display_name
    if is_ephemeral_skill_path(entity_id):
        fields["stored_path_ephemeral"] = True
    fields.update(
        resolve_skill_origin_fields(
            source_path=fields.get("source_path"),
            workspace_root=workspace_root,
        ),
    )
    return fields


def _tool_status_fields(
    state: EntityTierState,
    *,
    config: dict[str, Any] | None,
    workspace_root: Path | None = None,
) -> dict[str, Any]:
    return resolve_tool_origin_fields(
        state.entity_id,
        config=config,
        workspace_root=workspace_root,
    )


def entity_status_dict(
    state: EntityTierState,
    *,
    cfg: TierSectionConfig,
    session_id: int,
    now_ms: int | None = None,
    effective_tier_fn: Callable[[EntityTierState], Tier] | None = None,
    kind: str | None = None,
    config: dict[str, Any] | None = None,
    workspace_root: Path | None = None,
) -> dict[str, Any]:
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    resolve_effective = effective_tier_fn or (lambda s: effective_tier_for(s, now_ms=now_ms))
    effective = resolve_effective(state)
    base = state.stable_tier
    temporary_tier: str | None = tier_label(effective) if effective > base else None
    overlap: str | None = tier_label(state.overlap_tier) if state.overlap_tier is not None else None
    temporary = (
        state.temp_promotion_until_ms is not None
        or state.overlap_tier is not None
        or state.effective_tier != state.stable_tier
        or effective > base
    )
    expires_in_sec: int | None = None
    if state.temp_promotion_until_ms is not None and now_ms < state.temp_promotion_until_ms:
        expires_in_sec = max(0, (state.temp_promotion_until_ms - now_ms) // 1000)

    record: dict[str, Any] = {
        "entity_id": state.entity_id,
        "base_tier": tier_label(base),
        "effective_tier": tier_label(effective),
        "temporary_tier": temporary_tier,
        "overlap_tier": overlap,
        "temporary": temporary,
        "temp_promotion_until_ms": state.temp_promotion_until_ms,
        "temp_promotion_expires_in_sec": expires_in_sec,
        "tier_since_epoch": state.tier_since_epoch,
        "wake_lease_until_session": state.wake_lease_until_session,
        "sleep_cooldown_until_session": state.sleep_cooldown_until_session,
        "pipeline": state.pipeline,
        "policy": _policy_for_tier(effective),
        "stats": _stats_dict(state.stats),
        "scores": {
            "demand": demand_score(state.stats),
            "utility": utility_score(state.stats),
            "shadow": shadow_score(state.stats),
            "wake_pressure": wake_pressure(state, cfg=cfg),
        },
        "hints": _placement_hints(
            state,
            cfg=cfg,
            session_id=session_id,
            now_ms=now_ms,
            effective=effective,
        ),
    }
    if kind == EntityKind.SKILL:
        record.update(
            _skill_status_fields(state, config=config, workspace_root=workspace_root),
        )
    elif kind == EntityKind.TOOL:
        record.update(
            _tool_status_fields(state, config=config, workspace_root=workspace_root),
        )
    return record


def build_kind_detail(
    states: dict[tuple[str, str], EntityTierState],
    *,
    kind: str,
    cfg: TierSectionConfig,
    session_id: int,
    now_ms: int | None = None,
    effective_tier_fn: Callable[[EntityTierState], Tier] | None = None,
    config: dict[str, Any] | None = None,
    workspace_root: Path | None = None,
) -> dict[str, Any]:
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    resolve_effective = effective_tier_fn or (lambda s: effective_tier_for(s, now_ms=now_ms))
    histogram: dict[str, int] = dict.fromkeys(_TIER_LABELS, 0)
    by_tier: dict[str, list[dict[str, Any]]] = {label: [] for label in _TIER_LABELS}

    for key, state in states.items():
        if key[0] != kind:
            continue
        if kind == EntityKind.SKILL and is_ephemeral_skill_path(state.entity_id):
            continue
        effective = resolve_effective(state)
        label = tier_label(effective)
        histogram[label] = histogram.get(label, 0) + 1
        by_tier.setdefault(label, []).append(
            entity_status_dict(
                state,
                cfg=cfg,
                session_id=session_id,
                now_ms=now_ms,
                effective_tier_fn=resolve_effective,
                kind=kind,
                config=config,
                workspace_root=workspace_root,
            ),
        )

    for entities in by_tier.values():
        entities.sort(key=lambda item: str(item.get("entity_id", "")))

    return {
        "histogram": {label: histogram.get(label, 0) for label in _TIER_LABELS},
        "by_tier": {label: by_tier.get(label, []) for label in _TIER_LABELS},
    }


def _path_is_under_workspace(path: Path, workspace_root: Path) -> bool:
    try:
        path.resolve().relative_to(workspace_root.resolve())
    except ValueError:
        return False
    return True


def _tracked_skill_entity_ids(
    states: dict[tuple[str, str], EntityTierState],
) -> set[str]:
    tracked: set[str] = set()
    for key, state in states.items():
        if key[0] != EntityKind.SKILL:
            continue
        if is_ephemeral_skill_path(state.entity_id):
            continue
        tracked.add(state.entity_id)
    return tracked


def _discover_workspace_skill_paths(
    config: dict[str, Any],
    workspace_root: Path,
    *,
    agent: str,
) -> list[Path]:
    from cyt.skills.catalog import _walk_skill_md_files
    from cyt.skills.directories import resolve_skill_directories

    directories = [
        str(directory)
        for directory in resolve_skill_directories(
            config,
            agent=agent,
            workspace_root=workspace_root,
        )
    ]

    paths: list[Path] = []
    for path in _walk_skill_md_files(directories):
        if _path_is_under_workspace(path, workspace_root):
            paths.append(path)
    return paths


def filter_skill_detail_by_agent(
    detail: dict[str, Any],
    *,
    agent: str,
    config: dict[str, Any],
    workspace_root: Path | None,
) -> dict[str, Any]:
    """Keep only skill rows visible for *agent* and rebuild histogram counts."""
    histogram: dict[str, int] = dict.fromkeys(_TIER_LABELS, 0)
    by_tier: dict[str, list[dict[str, Any]]] = {label: [] for label in _TIER_LABELS}
    raw_by_tier = detail.get("by_tier")
    if not isinstance(raw_by_tier, dict):
        return {
            "histogram": histogram,
            "by_tier": by_tier,
        }

    for label in _TIER_LABELS:
        items = raw_by_tier.get(label)
        if not isinstance(items, list):
            continue
        for entity in items:
            if not isinstance(entity, dict):
                continue
            if not skill_entity_visible_for_agent(
                entity,
                agent,
                config=config,
                workspace_root=workspace_root,
            ):
                continue
            by_tier[label].append(entity)
            histogram[label] = histogram.get(label, 0) + 1

    for entities in by_tier.values():
        entities.sort(key=lambda item: str(item.get("entity_id", "")))

    return {
        "histogram": {label: histogram.get(label, 0) for label in _TIER_LABELS},
        "by_tier": {label: by_tier.get(label, []) for label in _TIER_LABELS},
    }


def enrich_skill_detail_with_workspace_discoveries(
    detail: dict[str, Any],
    *,
    states: dict[tuple[str, str], EntityTierState],
    cfg: TierSectionConfig,
    config: dict[str, Any],
    workspace_root: Path | None,
    agent: str,
    session_id: int,
    now_ms: int | None = None,
    effective_tier_fn: Callable[[EntityTierState], Tier] | None = None,
) -> dict[str, Any]:
    """Add workspace-local skills from disk that are not yet tier-tracked."""
    if workspace_root is None or not workspace_root.is_dir():
        return detail

    if now_ms is None:
        now_ms = int(time.time() * 1000)
    resolve_effective = effective_tier_fn or (lambda s: effective_tier_for(s, now_ms=now_ms))

    histogram = dict(detail.get("histogram") or {})
    by_tier = {
        label: list(items)
        for label, items in (detail.get("by_tier") or {}).items()
        if isinstance(items, list)
    }
    for label in _TIER_LABELS:
        histogram.setdefault(label, 0)
        by_tier.setdefault(label, [])

    tracked = _tracked_skill_entity_ids(states)
    for path in _discover_workspace_skill_paths(config, workspace_root, agent=agent):
        canonical = str(path.resolve())
        doc_id = resolve_skill_doc_id(canonical)
        entity_id = tier_entity_id_for_skill(canonical, doc_id=doc_id)
        if not entity_id or entity_id in tracked:
            continue

        dormant = EntityTierState(
            entity_id=entity_id,
            kind=EntityKind.SKILL,
            stable_tier=Tier.DORMANT,
            effective_tier=Tier.DORMANT,
        )
        label = tier_label(resolve_effective(dormant))
        histogram[label] = histogram.get(label, 0) + 1
        by_tier.setdefault(label, []).append(
            entity_status_dict(
                dormant,
                cfg=cfg,
                session_id=session_id,
                now_ms=now_ms,
                effective_tier_fn=resolve_effective,
                kind=EntityKind.SKILL,
                config=config,
                workspace_root=workspace_root,
            ),
        )
        tracked.add(entity_id)

    for entities in by_tier.values():
        entities.sort(key=lambda item: str(item.get("entity_id", "")))

    return {
        "histogram": {label: histogram.get(label, 0) for label in _TIER_LABELS},
        "by_tier": {label: by_tier.get(label, []) for label in _TIER_LABELS},
    }


def build_combined_histogram(
    states: dict[tuple[str, str], EntityTierState],
    *,
    now_ms: int | None = None,
    effective_tier_fn: Callable[[EntityTierState], Tier] | None = None,
) -> dict[str, dict[str, int]]:
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    resolve_effective = effective_tier_fn or (lambda s: effective_tier_for(s, now_ms=now_ms))
    out: dict[str, dict[str, int]] = {}
    for (kind, _), state in states.items():
        label = tier_label(resolve_effective(state))
        kind_hist = out.setdefault(str(kind), dict.fromkeys(_TIER_LABELS, 0))
        kind_hist[label] = kind_hist.get(label, 0) + 1
    return out
