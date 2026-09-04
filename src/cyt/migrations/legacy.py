"""Read-time normalization for legacy config.yaml key paths."""

from __future__ import annotations

import copy
from typing import Any

from cyt.migrations.base import read_schema_version
from cyt.migrations.versions import load_revision_modules

# Apply legacy shim only for configs not yet past revision 002.
_LEGACY_SHIM_CUTOFF = "002_pruning_tools_namespace"


def _revision_sort_key(revision_id: str) -> tuple[int, str]:
    if revision_id == "000_baseline":
        return (0, revision_id)
    prefix = revision_id.split("_", 1)[0]
    try:
        return (int(prefix), revision_id)
    except ValueError:
        return (9999, revision_id)


def _needs_legacy_shim(cfg: dict[str, Any]) -> bool:
    current = read_schema_version(cfg)
    return _revision_sort_key(current) < _revision_sort_key(_LEGACY_SHIM_CUTOFF)


def normalize_legacy_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Apply read-time aliases for unmigrated configs (does not persist)."""
    if not _needs_legacy_shim(cfg):
        return cfg
    modules = load_revision_modules()
    upgrade_002 = next(
        (module.upgrade for module in modules if module.revision == _LEGACY_SHIM_CUTOFF),
        None,
    )
    if upgrade_002 is None:
        return cfg
    normalized = upgrade_002(copy.deepcopy(cfg), scope="global")
    # Do not stamp schema_version on read — disk file stays unmigrated until runner runs.
    cyt = normalized.get("cyt")
    if isinstance(cyt, dict):
        cyt.pop("schema_version", None)
        cyt.pop("migrated_at", None)
        if not cyt:
            normalized.pop("cyt", None)
    return normalized
