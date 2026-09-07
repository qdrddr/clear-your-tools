"""Config schema revision 007 — promote tools, relocate agent MCP settings."""

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

revision = "007_config_layout_restructure"
down_revision = "006_policies_stubs_schema"
applies_to = "both"

_INJECT_VIA_AGENTS = ("cursor", "claude", "codex")


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


def _migrate_inject_via(cfg: dict[str, Any]) -> None:
    pruning = cfg.get("pruning")
    if not isinstance(pruning, dict):
        return
    inject_via = pruning.get("inject_via")
    if not isinstance(inject_via, dict):
        return
    agents = ensure_dict(cfg, "agents")
    for agent in _INJECT_VIA_AGENTS:
        mode = inject_via.get(agent)
        if mode is None:
            continue
        agent_block = ensure_dict(agents, agent)
        tools = ensure_dict(agent_block, "tools")
        if tools.get("inject_via") is None:
            tools["inject_via"] = mode
    pruning.pop("inject_via", None)


def _migrate_agent_mcp_to_tools(cfg: dict[str, Any]) -> None:
    agents = cfg.get("agents")
    if not isinstance(agents, dict):
        return
    for agent_block in agents.values():
        if not isinstance(agent_block, dict):
            continue
        legacy = agent_block.get("mcp")
        if not isinstance(legacy, dict):
            continue
        tools = agent_block.get("tools")
        if not isinstance(tools, dict):
            agent_block["tools"] = copy.deepcopy(legacy)
        else:
            for key, value in legacy.items():
                tools.setdefault(key, value)
        agent_block.pop("mcp", None)


def _migrate_cyt_mcp_agent(cfg: dict[str, Any]) -> None:
    agent = get_path(cfg, "pruning", "tools", "hook", "cyt_mcp", "agent")
    if agent is None:
        agent = get_path(cfg, "tools", "hook", "cyt_mcp", "agent")
    if agent is None:
        return
    defaults = ensure_dict(cfg, "defaults")
    if defaults.get("cyt_mcp_agent") is None:
        defaults["cyt_mcp_agent"] = agent
    hook_cyt = get_path(cfg, "tools", "hook", "cyt_mcp")
    if isinstance(hook_cyt, dict):
        hook_cyt.pop("agent", None)
    legacy_hook = get_path(cfg, "pruning", "tools", "hook", "cyt_mcp")
    if isinstance(legacy_hook, dict):
        legacy_hook.pop("agent", None)


def _migrate_cursor_rule_file(cfg: dict[str, Any]) -> None:
    enabled = get_path(cfg, "skills", "hook", "cursor_rule_file", "enabled")
    if enabled is None:
        return
    cursor = ensure_dict(ensure_dict(cfg, "agents"), "cursor")
    hook = ensure_dict(cursor, "hook")
    rule = hook.get("cursor_rule_file")
    if not isinstance(rule, dict):
        rule = {}
        hook["cursor_rule_file"] = rule
    if rule.get("enabled") is None:
        rule["enabled"] = enabled
    skills = cfg.get("skills")
    if isinstance(skills, dict):
        hook_block = skills.get("hook")
        if isinstance(hook_block, dict):
            hook_block.pop("cursor_rule_file", None)


def _prune_empty_pruning(cfg: dict[str, Any]) -> None:
    pruning = cfg.get("pruning")
    if isinstance(pruning, dict) and not pruning:
        cfg.pop("pruning", None)


def upgrade(cfg: dict[str, Any], *, scope: ConfigScope) -> dict[str, Any]:
    del scope
    result = deep_copy_config(cfg)

    # pruning.tools → tools (007 canonical root key)
    _move_if_absent(result, ("pruning", "tools"), ("tools",))
    _move_if_absent(result, ("pruning", "max_batch_workers"), ("tools", "max_batch_workers"))
    _move_if_absent(result, ("mcp", "permissions"), ("tools", "permissions"))

    _move_if_absent(result, ("hallucination_gate",), ("defaults", "hallucination_gate"))
    _move_if_absent(result, ("pruning", "inject_via_default"), ("defaults", "inject_via_default"))

    _migrate_inject_via(result)
    _migrate_agent_mcp_to_tools(result)
    _migrate_cyt_mcp_agent(result)
    _migrate_cursor_rule_file(result)

    # Drop empty legacy containers after moves.
    mcp = result.get("mcp")
    if isinstance(mcp, dict) and not mcp:
        result.pop("mcp", None)
    _prune_empty_pruning(result)

    set_schema_stamp(result, revision)
    return result


def downgrade(cfg: dict[str, Any], *, scope: ConfigScope) -> dict[str, Any]:
    del scope
    raise NotImplementedError("downgrade not supported for 007_config_layout_restructure")
