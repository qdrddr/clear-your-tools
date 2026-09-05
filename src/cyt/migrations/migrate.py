"""On-disk config.yaml migration helpers."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

import yaml

from cyt.hook.install_scope import CytInstallScope
from cyt.migrations.base import ConfigScope, read_schema_version
from cyt.migrations.env import current_head
from cyt.migrations.runner import (
    MigrationResult,
    pending_revisions,
    upgrade_config_dict,
    write_backup,
)
from cyt.migrations.workspace_paths import (
    ensure_canonical_workspace_config,
    resolve_workspace_config_path,
)

logger = logging.getLogger(__name__)

_SKIP_ENV = "CYT_SKIP_CONFIG_MIGRATE"


def _load_yaml_dict(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        loaded = yaml.safe_load(f)
    return loaded if isinstance(loaded, dict) else {}


def skip_auto_migrate() -> bool:
    return os.environ.get(_SKIP_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def write_config_dict(path: Path, cfg: dict[str, Any]) -> None:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    content = yaml.dump(cfg, default_flow_style=False, sort_keys=False)
    path.write_text(content, encoding="utf-8")


def migrate_config_file(
    path: Path,
    *,
    scope: ConfigScope,
    dry_run: bool = False,
    target: str | None = None,
    backup: bool = True,
) -> MigrationResult | None:
    """Upgrade on-disk config.yaml to *target* (default HEAD). Returns None if file missing."""
    path = path.expanduser()
    if not path.is_file():
        return None

    raw = _load_yaml_dict(path)
    pending = pending_revisions(raw, scope=scope)
    if not pending and read_schema_version(raw) == (target or current_head()):
        return MigrationResult(
            cfg=raw,
            from_revision=read_schema_version(raw),
            to_revision=read_schema_version(raw),
            steps=(),
            changed=False,
        )

    result = upgrade_config_dict(raw, scope=scope, target=target)
    if not result.changed:
        return result

    if dry_run:
        return result

    backup_path: Path | None = None
    if backup:
        backup_path = write_backup(path)
    write_config_dict(path, result.cfg)
    if result.steps:
        logger.info(
            "Migrated %s %s -> %s (backup: %s)",
            path,
            result.from_revision,
            result.to_revision,
            backup_path or "none",
        )
    return result


def maybe_migrate_config_file(
    path: Path,
    *,
    scope: ConfigScope,
) -> dict[str, Any] | None:
    """Auto-migrate on load when behind HEAD; returns migrated dict or None if skipped/missing."""
    if skip_auto_migrate():
        return None
    path = path.expanduser()
    if not path.is_file():
        return None

    raw = _load_yaml_dict(path)
    if not pending_revisions(raw, scope=scope):
        return None

    try:
        result = migrate_config_file(path, scope=scope, dry_run=False, backup=True)
    except Exception:
        logger.exception("Config migration failed for %s; continuing with unmigrated config", path)
        return None

    return result.cfg if result is not None else None


def maybe_migrate_workspace_config(
    *,
    workspace_root: Path | None = None,
) -> Path | None:
    """Promote legacy workspace config and auto-migrate the canonical file."""
    scope = CytInstallScope(
        workspace_root=workspace_root or CytInstallScope.from_cwd().workspace_root,
    )
    path = resolve_workspace_config_path(scope)
    if path is None:
        return None
    maybe_migrate_config_file(path, scope="workspace")
    return path


def ensure_config_file_current(
    path: Path,
    *,
    scope: ConfigScope,
    create_if_missing: bool = False,
) -> MigrationResult | None:
    """Check schema version and migrate on-disk config to HEAD when behind.

    Prints to stderr only when migration runs or when pending migrations are
    blocked by ``CYT_SKIP_CONFIG_MIGRATE``.
    """
    path = path.expanduser()
    if not path.is_file():
        if create_if_missing:
            path.parent.mkdir(parents=True, exist_ok=True)
            write_config_dict(path, {})
        else:
            return None

    raw = _load_yaml_dict(path)
    pending = pending_revisions(raw, scope=scope)
    if not pending and read_schema_version(raw) == current_head():
        return MigrationResult(
            cfg=raw,
            from_revision=read_schema_version(raw),
            to_revision=read_schema_version(raw),
            steps=(),
            changed=False,
        )

    if skip_auto_migrate():
        if pending:
            print(
                f"config: {path} has pending migrations (CYT_SKIP_CONFIG_MIGRATE=1)",
                file=sys.stderr,
            )
        return None

    result = migrate_config_file(path, scope=scope, dry_run=False, backup=True)
    if result is not None and result.changed:
        print(
            f"config: migrated {path} {result.from_revision} -> {result.to_revision}",
            file=sys.stderr,
        )
        if result.steps:
            print(f"  steps: {', '.join(result.steps)}", file=sys.stderr)
    return result


def ensure_workspace_config_current(
    *,
    workspace_root: Path | None = None,
) -> Path | None:
    """Promote legacy workspace config and migrate to HEAD when behind."""
    scope = CytInstallScope(
        workspace_root=workspace_root or CytInstallScope.from_cwd().workspace_root,
    )
    if not scope.has_workspace:
        return None
    path = ensure_canonical_workspace_config(scope)
    if path is None or not path.is_file():
        return path
    ensure_config_file_current(path, scope="workspace")
    return path
