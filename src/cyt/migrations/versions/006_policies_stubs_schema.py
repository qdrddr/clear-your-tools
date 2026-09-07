"""Config schema revision 006 — declarative policies catalog (bundled defaults)."""

from __future__ import annotations

from typing import Any

from cyt.migrations.base import (
    ConfigScope,
    deep_copy_config,
    get_path,
    pop_path,
    set_path,
    set_schema_stamp,
)

revision = "006_policies_stubs_schema"
down_revision = "005_skills_agent_directories"
applies_to = "both"


def _move_if_absent(
    cfg: dict[str, Any],
    src: tuple[str, ...],
    dst: tuple[str, ...],
) -> None:
    if get_path(cfg, *dst) is not None:
        return
    value = pop_path(cfg, *src)
    if value is not None:
        set_path(cfg, value, *dst)


def upgrade(cfg: dict[str, Any], *, scope: ConfigScope) -> dict[str, Any]:
    del scope
    result = deep_copy_config(cfg)

    # Belt-and-suspenders after 002 for configs that skipped intermediate migration.
    _move_if_absent(result, ("pruning", "policy"), ("pruning", "tools", "policy"))
    _move_if_absent(result, ("pruning", "per_tool"), ("pruning", "tools", "policy", "per_tool"))

    # Policy definitions live in bundled defaults.yaml — do not materialize into user files.
    # Preserve any user-defined policies[] overlay as-is.

    set_schema_stamp(result, revision)
    return result


def downgrade(cfg: dict[str, Any], *, scope: ConfigScope) -> dict[str, Any]:
    del scope
    raise NotImplementedError("downgrade not supported for 006_policies_stubs_schema")
