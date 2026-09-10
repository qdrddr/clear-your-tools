"""Tier configuration resolution."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cyt.config.sections import tools_at
from cyt.hook.install_scope import CytInstallScope
from cyt.tiers.models import TierScope


@dataclass(frozen=True)
class TierThresholds:
    promote_demand: float
    demote_demand: float
    promote_utility: float
    demote_utility: float


@dataclass(frozen=True)
class TierSectionConfig:
    enabled: bool
    shadow: bool
    request_half_life: float
    prompt_cache_ttl_minutes: float
    ttl_multiplier: float
    idle_gap_triggers_epoch: bool
    min_injections_before_reconsider: int
    wake_threshold: float
    wake_lease_sessions: int
    sleep_cooldown_sessions: int
    emergency_t4_inject_min: int
    emergency_t4_utility_max: float
    temp_promotion_turns: int
    thresholds_t12: TierThresholds
    thresholds_t23: TierThresholds
    thresholds_t34: TierThresholds
    wake_weights: dict[str, float]


def _float(value: object, default: float) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return default


def _int(value: object, default: int) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return default


def _bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    return default


def _threshold_pair(section: dict[str, Any], prefix: str) -> TierThresholds:
    return TierThresholds(
        promote_demand=_float(section.get(f"{prefix}_promote_demand"), 0.55),
        demote_demand=_float(section.get(f"{prefix}_demote_demand"), 0.30),
        promote_utility=_float(section.get(f"{prefix}_promote_utility"), 0.35),
        demote_utility=_float(section.get(f"{prefix}_demote_utility"), 0.20),
    )


def _tiers_block(cfg: dict[str, Any], *, kind: str) -> dict[str, Any]:
    if kind == "tool":
        block = tools_at(cfg, "tiers")
    else:
        skills = cfg.get("skills")
        block = skills.get("tiers") if isinstance(skills, dict) else None
    if isinstance(block, dict):
        return block
    return {}


def _merge_tiers_blocks(cfg: dict[str, Any], *, kind: str) -> dict[str, Any]:
    tools_block = _tiers_block(cfg, kind="tool")
    if kind == "tool":
        return dict(tools_block)
    skills_block = _tiers_block(cfg, kind="skill")
    merged = dict(tools_block)
    merged.update(skills_block)
    return merged


def tier_section_config(cfg: dict[str, Any], *, kind: str) -> TierSectionConfig:
    block = _merge_tiers_blocks(cfg, kind=kind)
    stats = block.get("statistics")
    stats_dict = stats if isinstance(stats, dict) else {}
    epoch = block.get("cache_epoch")
    epoch_dict = epoch if isinstance(epoch, dict) else {}
    evaluation = block.get("evaluation")
    eval_dict = evaluation if isinstance(evaluation, dict) else {}
    wake = block.get("wake")
    wake_dict = wake if isinstance(wake, dict) else {}
    thresholds = block.get("thresholds")
    thresholds_dict = thresholds if isinstance(thresholds, dict) else {}
    t12 = thresholds_dict.get("t12")
    t23 = thresholds_dict.get("t23")
    t34 = thresholds_dict.get("t34")
    t12_dict = t12 if isinstance(t12, dict) else {}
    t23_dict = t23 if isinstance(t23, dict) else {}
    t34_dict = t34 if isinstance(t34, dict) else {}
    wake_weights = wake_dict.get("weights")
    weights = wake_weights if isinstance(wake_weights, dict) else {}
    kind_block = _tiers_block(cfg, kind=kind)
    enabled = _bool(kind_block.get("enabled"), _bool(block.get("enabled"), False))
    shadow = _bool(kind_block.get("shadow"), _bool(block.get("shadow"), True))
    return TierSectionConfig(
        enabled=enabled,
        shadow=shadow,
        request_half_life=_float(stats_dict.get("request_half_life"), 100.0),
        prompt_cache_ttl_minutes=_float(epoch_dict.get("prompt_cache_ttl_minutes"), 5.0),
        ttl_multiplier=_float(epoch_dict.get("ttl_multiplier"), 1.0),
        idle_gap_triggers_epoch=_bool(epoch_dict.get("idle_gap_triggers_epoch"), True),
        min_injections_before_reconsider=_int(
            eval_dict.get("min_injections_before_reconsider"),
            8,
        ),
        wake_threshold=_float(wake_dict.get("threshold"), 0.60),
        wake_lease_sessions=_int(wake_dict.get("lease_sessions"), 2),
        sleep_cooldown_sessions=_int(wake_dict.get("cooldown_sessions"), 2),
        emergency_t4_inject_min=_int(block.get("emergency_t4_inject_min"), 20),
        emergency_t4_utility_max=_float(block.get("emergency_t4_utility_max"), 0.20),
        temp_promotion_turns=_int(block.get("temp_promotion_turns"), 3),
        thresholds_t12=_threshold_pair(t12_dict, "t12"),
        thresholds_t23=_threshold_pair(t23_dict, "t23"),
        thresholds_t34=_threshold_pair(t34_dict, "t34"),
        wake_weights={
            "shadow": _float(weights.get("shadow"), 0.40),
            "direct": _float(weights.get("direct"), 0.30),
            "query": _float(weights.get("query"), 0.20),
            "sibling": _float(weights.get("sibling"), 0.10),
        },
    )


def tier_state_db_path(cfg: dict[str, Any]) -> str:
    block = _tiers_block(cfg, kind="tool")
    database = block.get("database")
    if isinstance(database, dict):
        path = database.get("path")
        if isinstance(path, str) and path.strip():
            return str(Path(path).expanduser())
    return str(Path("~/.config/cyt/tier_state.db").expanduser())


def resolve_tier_scope(*, workspace: Path | None = None) -> TierScope:

    user_key = os.environ.get("USER") or os.environ.get("USERNAME") or "default"
    scope = CytInstallScope.from_cwd(cwd=workspace)
    workspace_key = str(scope.workspace_root) if scope.workspace_root else ""
    return TierScope(user_key=user_key, workspace_key=workspace_key)


def tiers_active(cfg: dict[str, Any], *, kind: str) -> bool:
    section = tier_section_config(cfg, kind=kind)
    return section.enabled or section.shadow


def tiers_apply(cfg: dict[str, Any], *, kind: str) -> bool:
    section = tier_section_config(cfg, kind=kind)
    return section.enabled and not section.shadow
