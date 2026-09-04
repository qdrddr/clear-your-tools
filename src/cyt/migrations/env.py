"""Revision registry for config migrations (Alembic-style linear chain)."""

from __future__ import annotations

from dataclasses import dataclass

from cyt.migrations.base import (
    BASELINE_REVISION,
    AppliesTo,
    ConfigMigrationFn,
)
from cyt.migrations.versions import load_revision_modules

UpgradeFn = ConfigMigrationFn
DowngradeFn = ConfigMigrationFn


@dataclass(frozen=True)
class MigrationRevision:
    revision: str
    down_revision: str
    applies_to: AppliesTo
    upgrade: UpgradeFn
    downgrade: DowngradeFn


def _build_revisions() -> tuple[MigrationRevision, ...]:
    items: list[MigrationRevision] = []
    for module in load_revision_modules():
        items.append(
            MigrationRevision(
                revision=module.revision,
                down_revision=module.down_revision,
                applies_to=module.applies_to,
                upgrade=module.upgrade,
                downgrade=module.downgrade,
            ),
        )
    return tuple(items)


_REVISIONS: tuple[MigrationRevision, ...] = _build_revisions()


def all_revisions() -> tuple[MigrationRevision, ...]:
    return _REVISIONS


def revision_by_id(revision_id: str) -> MigrationRevision | None:
    for item in _REVISIONS:
        if item.revision == revision_id:
            return item
    return None


def current_head() -> str:
    if not _REVISIONS:
        return BASELINE_REVISION
    return _REVISIONS[-1].revision


def revision_chain() -> list[MigrationRevision]:
    """Return revisions in upgrade order."""
    by_down: dict[str, MigrationRevision] = {item.down_revision: item for item in _REVISIONS}
    ordered: list[MigrationRevision] = []
    cursor = BASELINE_REVISION
    while cursor in by_down:
        nxt = by_down[cursor]
        ordered.append(nxt)
        cursor = nxt.revision
    if len(ordered) != len(_REVISIONS):
        raise RuntimeError("config migration revision chain is broken or branched")
    return ordered
