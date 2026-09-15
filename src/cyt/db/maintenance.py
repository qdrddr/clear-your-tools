"""Unified SQLite maintenance for CYT local databases."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DbTableMaintenanceResult:
    deleted: dict[str, int] = field(default_factory=dict)
    updated: dict[str, int] = field(default_factory=dict)
    vacuumed: bool = False
    dry_run: bool = False

    def total_deleted(self) -> int:
        return sum(self.deleted.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "deleted": dict(self.deleted),
            "updated": dict(self.updated),
            "vacuumed": self.vacuumed,
            "dry_run": self.dry_run,
        }


@dataclass
class DbMaintenanceResult:
    tool_examples: DbTableMaintenanceResult | None = None
    tier_state: DbTableMaintenanceResult | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if self.tool_examples is not None:
            payload["tool_examples"] = self.tool_examples.to_dict()
        if self.tier_state is not None:
            payload["tier_state"] = self.tier_state.to_dict()
        return payload


def run_cyt_db_maintenance(
    config: dict[str, Any],
    *,
    dry_run: bool = False,
    vacuum: bool | None = None,
) -> DbMaintenanceResult:
    """Run maintenance for all enabled CYT SQLite databases."""
    from cyt.tool_examples.config import examples_active
    from cyt.tiers.config import tiers_active

    result = DbMaintenanceResult()
    if examples_active(config):
        from cyt.tool_examples.maintenance import run_tool_examples_maintenance

        result.tool_examples = run_tool_examples_maintenance(
            config,
            dry_run=dry_run,
            vacuum=vacuum,
        )
    if tiers_active(config, kind="tool"):
        from cyt.tiers.maintenance import run_tier_state_maintenance

        result.tier_state = run_tier_state_maintenance(
            config,
            dry_run=dry_run,
            vacuum=vacuum,
        )
    return result
