"""Tier database retention and maintenance configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cyt.tiers.config import _bool, _float, _int, _tiers_block, tier_section_config


@dataclass(frozen=True)
class TierRetentionConfig:
    enabled: bool
    maintenance_interval_seconds: float
    apply_decay_on_maintenance: bool
    request_half_life: float
    entity_max_idle_days: int
    entity_counter_floor: float
    epoch_log_max_entries: int
    epoch_log_max_age_days: int
    vacuum_after_maintenance: bool


def tier_retention_config(cfg: dict[str, Any]) -> TierRetentionConfig:
    block = _tiers_block(cfg, kind="tool")
    retention = block.get("retention")
    retention_dict = retention if isinstance(retention, dict) else {}
    stats_cfg = tier_section_config(cfg, kind="tool")
    half_life_raw = retention_dict.get("request_half_life")
    if isinstance(half_life_raw, (int, float)):
        request_half_life = float(half_life_raw)
    else:
        request_half_life = stats_cfg.request_half_life
    return TierRetentionConfig(
        enabled=_bool(retention_dict.get("enabled"), True),
        maintenance_interval_seconds=_float(
            retention_dict.get("maintenance_interval_seconds"),
            3600.0,
        ),
        apply_decay_on_maintenance=_bool(
            retention_dict.get("apply_decay_on_maintenance"),
            True,
        ),
        request_half_life=request_half_life,
        entity_max_idle_days=_int(retention_dict.get("entity_max_idle_days"), 180),
        entity_counter_floor=_float(retention_dict.get("entity_counter_floor"), 0.01),
        epoch_log_max_entries=_int(retention_dict.get("epoch_log_max_entries"), 200),
        epoch_log_max_age_days=_int(retention_dict.get("epoch_log_max_age_days"), 90),
        vacuum_after_maintenance=_bool(retention_dict.get("vacuum_after_maintenance"), True),
    )
