"""Config.yaml schema migrations (Alembic-inspired)."""

from __future__ import annotations

from cyt.migrations.env import current_head, revision_chain
from cyt.migrations.legacy import normalize_legacy_config
from cyt.migrations.migrate import (
    ensure_config_file_current,
    ensure_workspace_config_current,
    maybe_migrate_config_file,
    maybe_migrate_workspace_config,
    migrate_config_file,
    skip_auto_migrate,
    write_config_dict,
)
from cyt.migrations.providers import normalize_provider_entry, provider_registry_index
from cyt.migrations.runner import (
    MigrationResult,
    migration_history,
    pending_revisions,
    upgrade_config_dict,
    write_backup,
)
from cyt.migrations.workspace_paths import (
    ensure_canonical_workspace_aggregator,
    ensure_canonical_workspace_config,
    ensure_canonical_workspace_server_defs,
    resolve_workspace_config_path,
)

__all__ = [
    "MigrationResult",
    "current_head",
    "ensure_canonical_workspace_aggregator",
    "ensure_canonical_workspace_config",
    "ensure_canonical_workspace_server_defs",
    "ensure_config_file_current",
    "ensure_workspace_config_current",
    "maybe_migrate_config_file",
    "maybe_migrate_workspace_config",
    "migrate_config_file",
    "migration_history",
    "normalize_legacy_config",
    "normalize_provider_entry",
    "pending_revisions",
    "provider_registry_index",
    "resolve_workspace_config_path",
    "revision_chain",
    "skip_auto_migrate",
    "upgrade_config_dict",
    "write_backup",
    "write_config_dict",
]
