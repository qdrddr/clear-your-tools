"""Move legacy pruning.* keys to pruning.tools.* canonical namespace."""

from __future__ import annotations

import copy
from typing import Any

from cyt.migrations.base import (
    ConfigScope,
    deep_copy_config,
    ensure_dict,
    get_path,
    pop_path,
    set_path,
    set_schema_stamp,
)

revision = "002_pruning_tools_namespace"
down_revision = "001_add_schema_version"
applies_to = "both"

_STAGE_NAMES = ("bm25", "rerank", "llm")


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


def _migrate_stage_pipelines(cfg: dict[str, Any]) -> None:
    pruning = cfg.get("pruning")
    if not isinstance(pruning, dict):
        return
    pipelines = ensure_dict(pruning, "tools", "pipelines")
    for stage in _STAGE_NAMES:
        stage_cfg = pruning.get(stage)
        if not isinstance(stage_cfg, dict):
            continue
        if stage in pipelines and isinstance(pipelines.get(stage), dict):
            pruning.pop(stage, None)
            continue
        migrated = copy.deepcopy(stage_cfg)
        model_remote = get_path(migrated, "model", "remote", "model_nick")
        if model_remote is not None and get_path(migrated, "model_nick") is None:
            migrated["model_nick"] = model_remote
            model = migrated.get("model")
            if isinstance(model, dict):
                remote = model.get("remote")
                if isinstance(remote, dict):
                    remote.pop("model_nick", None)
                if not remote:
                    model.pop("remote", None)
                if not model:
                    migrated.pop("model", None)
        pipelines[stage] = migrated
        pruning.pop(stage, None)


def upgrade(cfg: dict[str, Any], *, scope: ConfigScope) -> dict[str, Any]:
    del scope
    result = deep_copy_config(cfg)
    _move_if_absent(result, ("pruning", "pipeline"), ("pruning", "tools", "sequence"))
    _move_if_absent(result, ("pruning", "policy"), ("pruning", "tools", "policy"))
    _move_if_absent(result, ("pruning", "per_tool"), ("pruning", "tools", "policy", "per_tool"))

    tools = get_path(result, "pruning", "tools")
    if isinstance(tools, dict):
        inject_via = tools.get("inject_via")
        if (
            isinstance(inject_via, str)
            and get_path(result, "pruning", "inject_via_default") is None
        ):
            set_path(result, inject_via, "pruning", "inject_via_default")
            tools.pop("inject_via", None)

    _migrate_stage_pipelines(result)
    set_schema_stamp(result, revision)
    return result


def downgrade(cfg: dict[str, Any], *, scope: ConfigScope) -> dict[str, Any]:
    del scope
    raise NotImplementedError("downgrade not supported for 002_pruning_tools_namespace")
