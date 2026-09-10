"""Thin feedback hooks for tools and skills tier statistics."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def record_tools_injected_feedback(
    tools: list[dict[str, Any]] | None,
    *,
    config: dict[str, Any] | None,
) -> None:
    if not tools or config is None:
        return
    try:
        from cyt.config import load_config
        from cyt.hook.workspace_config import hook_workspace_from_config
        from cyt.tiers.config import tiers_active
        from cyt.tiers.manager import get_tier_manager

        cfg = config or load_config()
        if not tiers_active(cfg, kind="tool"):
            return
        get_tier_manager(cfg, workspace=hook_workspace_from_config(cfg)).record_tools_injected(
            tools,
            cfg,
        )
    except Exception:
        return


def record_tool_used_feedback(
    *,
    tool_name: str,
    catalog: str | None,
    config: dict[str, Any] | None = None,
    args: dict[str, Any] | None = None,
    workspace: Path | None = None,
    optional_used: bool | None = None,
) -> None:
    try:
        from cyt.config import load_config
        from cyt.hook.workspace_config import hook_workspace_from_config
        from cyt.tiers.config import tiers_active
        from cyt.tiers.manager import get_tier_manager

        cfg = config or load_config()
        if not tiers_active(cfg, kind="tool"):
            return
        resolved_workspace = workspace
        if resolved_workspace is None:
            resolved_workspace = hook_workspace_from_config(cfg)
        source = str(catalog or "unknown").strip()
        tool = {"name": tool_name, "cyt_catalog_source": source}
        optional = optional_used if optional_used is not None else _optional_properties_used(args)
        get_tier_manager(cfg, workspace=resolved_workspace).record_tool_used(
            tool,
            config=cfg,
            optional_used=optional,
        )
    except Exception:
        return


def record_skills_injected_feedback(
    matches: list[Any] | None,
    *,
    config: dict[str, Any] | None,
) -> None:
    if not matches or config is None:
        return
    try:
        from cyt.hook.workspace_config import hook_workspace_from_config
        from cyt.tiers.config import tiers_active
        from cyt.tiers.manager import get_tier_manager

        if not tiers_active(config, kind="skill"):
            return
        get_tier_manager(
            config,
            workspace=hook_workspace_from_config(config),
        ).record_skills_injected(
            matches,
            config,
        )
    except Exception:
        return


def record_skills_injected_feedback_from_md(
    skills_final_md: str | None,
    *,
    config: dict[str, Any] | None,
) -> None:
    if not skills_final_md or config is None:
        return
    paths: list[str] = []
    for line in skills_final_md.splitlines():
        stripped = line.strip()
        if 'path="' in stripped:
            start = stripped.find('path="') + len('path="')
            end = stripped.find('"', start)
            if end > start:
                paths.append(stripped[start:end])
    if not paths:
        return
    try:
        from cyt.hook.workspace_config import hook_workspace_from_config
        from cyt.tiers.adapters.skills import canonical_skill_entity_id
        from cyt.tiers.config import tiers_active
        from cyt.tiers.manager import get_tier_manager

        if not tiers_active(config, kind="skill"):
            return
        manager = get_tier_manager(config, workspace=hook_workspace_from_config(config))
        for path in paths:
            entity_id = canonical_skill_entity_id(path)
            if not entity_id:
                continue
            manager.record_skills_injected(
                [type("Match", (), {"file_path": entity_id})()],
                config,
            )
    except Exception:
        return


def record_skill_used_feedback(
    entity_id: str,
    *,
    config: dict[str, Any] | None,
    workspace: Path | None = None,
) -> None:
    if not entity_id or config is None:
        return
    try:
        from cyt.hook.workspace_config import hook_workspace_from_config
        from cyt.tiers.adapters.skills import canonical_skill_entity_id
        from cyt.tiers.config import tiers_active
        from cyt.tiers.manager import get_tier_manager

        if not tiers_active(config, kind="skill"):
            return
        resolved_workspace = workspace
        if resolved_workspace is None:
            resolved_workspace = hook_workspace_from_config(config)
        canonical_id = canonical_skill_entity_id(entity_id)
        if not canonical_id:
            return
        get_tier_manager(config, workspace=resolved_workspace).record_skill_used(
            canonical_id,
            config=config,
        )
    except Exception:
        return


def _optional_properties_used(args: dict[str, Any] | None) -> bool:
    if not isinstance(args, dict) or not args:
        return False
    # Heuristic: more than one key or any non-empty optional-looking field.
    if len(args) > 1:
        return True
    for value in args.values():
        if value not in (None, "", [], {}):
            return True
    return False
