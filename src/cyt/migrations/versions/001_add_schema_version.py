"""Add cyt.schema_version stamp to on-disk config.yaml."""

from __future__ import annotations

from typing import Any

from cyt.migrations.base import ConfigScope, deep_copy_config, set_schema_stamp

revision = "001_add_schema_version"
down_revision = "000_baseline"
applies_to = "both"


def upgrade(cfg: dict[str, Any], *, scope: ConfigScope) -> dict[str, Any]:
    del scope
    result = deep_copy_config(cfg)
    set_schema_stamp(result, revision)
    return result


def downgrade(cfg: dict[str, Any], *, scope: ConfigScope) -> dict[str, Any]:
    del scope
    result = deep_copy_config(cfg)
    cyt = result.get("cyt")
    if isinstance(cyt, dict):
        cyt.pop("schema_version", None)
        cyt.pop("migrated_at", None)
        if not cyt:
            result.pop("cyt", None)
    return result
