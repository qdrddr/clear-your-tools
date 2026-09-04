"""Run versioned config.yaml schema migrations."""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cyt.migrations.base import (
    BASELINE_REVISION,
    ConfigScope,
    applies_to_scope,
    deep_copy_config,
    read_schema_version,
)
from cyt.migrations.env import MigrationRevision, current_head, revision_chain

logger = logging.getLogger(__name__)

MAX_BACKUPS = 3


@dataclass(frozen=True)
class MigrationResult:
    cfg: dict[str, Any]
    from_revision: str
    to_revision: str
    steps: tuple[str, ...]
    changed: bool


def _revision_index(revision_id: str) -> int:
    chain = revision_chain()
    for index, item in enumerate(chain):
        if item.revision == revision_id:
            return index
    if revision_id == BASELINE_REVISION:
        return -1
    raise ValueError(f"unknown config schema revision: {revision_id!r}")


def _steps_to_target(
    current: str,
    target: str,
    *,
    scope: ConfigScope,
) -> list[MigrationRevision]:
    chain = revision_chain()
    start = _revision_index(current) + 1
    end = _revision_index(target)
    if end < start - 1:
        raise ValueError(f"cannot downgrade from {current!r} to {target!r} via upgrade()")
    steps: list[MigrationRevision] = []
    for item in chain[start : end + 1]:
        if applies_to_scope(item.applies_to, scope):
            steps.append(item)
    return steps


def upgrade_config_dict(
    cfg: dict[str, Any],
    *,
    scope: ConfigScope,
    target: str | None = None,
) -> MigrationResult:
    """Apply pending upgrade revisions to an in-memory config dict."""
    target_revision = target or current_head()
    current = read_schema_version(cfg)
    if current == target_revision:
        return MigrationResult(
            cfg=cfg,
            from_revision=current,
            to_revision=target_revision,
            steps=(),
            changed=False,
        )
    steps = _steps_to_target(current, target_revision, scope=scope)
    if not steps and current != target_revision:
        raise ValueError(
            f"no applicable migration steps from {current!r} to {target_revision!r} for scope {scope!r}",
        )
    working = deep_copy_config(cfg)
    applied: list[str] = []
    for step in steps:
        working = step.upgrade(working, scope=scope)
        applied.append(step.revision)
    changed = json.dumps(working, sort_keys=True, default=str) != json.dumps(
        cfg,
        sort_keys=True,
        default=str,
    )
    return MigrationResult(
        cfg=working,
        from_revision=current,
        to_revision=read_schema_version(working),
        steps=tuple(applied),
        changed=changed,
    )


def write_backup(path: Path) -> Path | None:
    if not path.is_file():
        return None
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_path = path.with_name(f"{path.name}.bak.{timestamp}")
    shutil.copy2(path, backup_path)
    _rotate_backups(path)
    return backup_path


def _rotate_backups(path: Path) -> None:
    backups = sorted(path.parent.glob(f"{path.name}.bak.*"), reverse=True)
    for old in backups[MAX_BACKUPS:]:
        try:
            old.unlink()
        except OSError:
            pass


def migration_history() -> list[tuple[str, str, str]]:
    """Return (revision, down_revision, applies_to) tuples in order."""
    return [(item.revision, item.down_revision, item.applies_to) for item in revision_chain()]


def pending_revisions(cfg: dict[str, Any], *, scope: ConfigScope) -> list[str]:
    current = read_schema_version(cfg)
    head = current_head()
    if current == head:
        return []
    try:
        steps = _steps_to_target(current, head, scope=scope)
    except ValueError:
        return []
    return [step.revision for step in steps]
