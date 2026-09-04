"""Revision scaffold for ``cyt config revision`` (future).

Copy to src/cyt/migrations/versions/00N_short_description.py and register
the module name in versions/__init__.py _REVISION_MODULE_NAMES.
"""

from __future__ import annotations

from typing import Any

from cyt.migrations.base import ConfigScope, deep_copy_config, set_schema_stamp

revision = "00N_short_description"
down_revision = "004_permissions_agents_layout"
applies_to = "both"


def upgrade(cfg: dict[str, Any], *, scope: ConfigScope) -> dict[str, Any]:
    del scope
    result = deep_copy_config(cfg)
    # ... structural changes ...
    set_schema_stamp(result, revision)
    return result


def downgrade(cfg: dict[str, Any], *, scope: ConfigScope) -> dict[str, Any]:
    del scope
    raise NotImplementedError(f"downgrade not supported for {revision}")
