"""Canonical config section resolution with legacy path fallbacks."""

from __future__ import annotations

from typing import Any

INJECT_VIA_AGENT_NAMES = ("cursor", "claude", "codex")


def _nested_dict_value(root: dict[str, Any], *keys: str) -> object | None:
    if not keys:
        return None
    current: object = root
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _deep_merge_dict(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge_dict(result[key], value)
        else:
            result[key] = value
    return result


def tools_dict(cfg: dict[str, Any]) -> dict[str, Any]:
    """Top-level ``tools`` section merged with legacy ``pruning.tools`` overlay."""
    canonical = cfg.get("tools")
    legacy: dict[str, Any] | None = None
    pruning = cfg.get("pruning")
    if isinstance(pruning, dict):
        raw_legacy = pruning.get("tools")
        if isinstance(raw_legacy, dict):
            legacy = raw_legacy
    if isinstance(canonical, dict) and legacy is not None:
        return _deep_merge_dict(canonical, legacy)
    if isinstance(canonical, dict):
        return canonical
    if legacy is not None:
        return legacy
    return {}


def tools_at(cfg: dict[str, Any], *keys: str) -> object | None:
    value = _nested_dict_value(tools_dict(cfg), *keys)
    return value if value is not None else None


def tools_value(cfg: dict[str, Any], *keys: str) -> object | None:
    """Read from ``tools`` with legacy ``pruning.tools`` fallback on the same dict."""
    value = tools_at(cfg, *keys)
    if value is not None:
        return value
    return _nested_dict_value(cfg, "pruning", "tools", *keys)


def global_mcp_permissions_raw(cfg: dict[str, Any]) -> dict[str, Any] | None:
    """Global MCP permission lists (``tools.permissions`` or legacy ``mcp.permissions``)."""
    tools = cfg.get("tools")
    if isinstance(tools, dict):
        permissions = tools.get("permissions")
        if isinstance(permissions, dict):
            return permissions
    mcp = cfg.get("mcp")
    if isinstance(mcp, dict):
        permissions = mcp.get("permissions")
        if isinstance(permissions, dict):
            return permissions
    return None


def agent_tools_block(cfg: dict[str, Any], agent: str) -> dict[str, Any] | None:
    """Per-agent tools overlay (``agents.<agent>.tools``, legacy ``agents.<agent>.mcp``)."""
    agents = cfg.get("agents")
    if not isinstance(agents, dict):
        return None
    agent_block = agents.get(agent)
    if not isinstance(agent_block, dict):
        return None
    tools = agent_block.get("tools")
    if isinstance(tools, dict):
        return tools
    legacy = agent_block.get("mcp")
    if isinstance(legacy, dict):
        return legacy
    return None


def agent_tools_inject_via(cfg: dict[str, Any], agent: str) -> str | None:
    """Per-agent inject_via from ``agents.<agent>.tools.inject_via`` (legacy ``.mcp``)."""
    tools = agent_tools_block(cfg, agent)
    if isinstance(tools, dict):
        value = tools.get("inject_via")
        if value is not None:
            text = str(value).strip().lower()
            if text in {"hook", "proxy"}:
                return text
    pruning = cfg.get("pruning")
    if isinstance(pruning, dict):
        inject_via = pruning.get("inject_via")
        if isinstance(inject_via, dict):
            value = inject_via.get(agent)
            if value is not None:
                text = str(value).strip().lower()
                if text in {"hook", "proxy"}:
                    return text
    return None


def agent_tools_permissions_raw(cfg: dict[str, Any], agent: str) -> dict[str, Any] | None:
    tools = agent_tools_block(cfg, agent)
    if not isinstance(tools, dict):
        return None
    permissions = tools.get("permissions")
    return permissions if isinstance(permissions, dict) else None


def _inject_via_in_pruning_map(cfg: dict[str, Any], agent: str) -> str | None:
    pruning = cfg.get("pruning")
    if not isinstance(pruning, dict):
        return None
    inject_via = pruning.get("inject_via")
    if not isinstance(inject_via, dict):
        return None
    value = inject_via.get(agent)
    if value is None:
        return None
    mode = str(value).strip().lower()
    return mode if mode in {"hook", "proxy"} else None


def _inject_via_in_agent_overlay(cfg: dict[str, Any], agent: str) -> str | None:
    agents = cfg.get("agents")
    if not isinstance(agents, dict):
        return None
    agent_block = agents.get(agent)
    if not isinstance(agent_block, dict):
        return None
    for key in ("tools", "mcp"):
        block = agent_block.get(key)
        if not isinstance(block, dict):
            continue
        value = block.get("inject_via")
        if value is None:
            continue
        mode = str(value).strip().lower()
        if mode in {"hook", "proxy"}:
            return mode
    return None


def inject_via_for_agent_from_config(
    cfg: dict[str, Any],
    agent: str,
    *,
    overlay: dict[str, Any] | None = None,
) -> str | None:
    """Resolve one agent's inject_via with user overlay priority over bundled defaults."""
    overlay_cfg = overlay if overlay is not None else cfg
    mode = _inject_via_in_agent_overlay(overlay_cfg, agent)
    if mode is not None:
        return mode
    mode = _inject_via_in_pruning_map(overlay_cfg, agent)
    if mode is not None:
        return mode
    return agent_tools_inject_via(cfg, agent)


def inject_via_map_from_config(
    cfg: dict[str, Any],
    *,
    overlay: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Build per-agent inject_via map from merged config."""
    overlay_cfg = overlay if overlay is not None else cfg
    result: dict[str, str] = {}
    for agent in INJECT_VIA_AGENT_NAMES:
        mode = inject_via_for_agent_from_config(cfg, agent, overlay=overlay_cfg)
        if mode is not None:
            result[agent] = mode
    if result:
        return result
    return inject_via_defaults_from_bundled(cfg)


def inject_via_defaults_from_bundled(cfg: dict[str, Any]) -> dict[str, str]:
    """Default inject_via per agent from bundled ``agents.*.tools.inject_via`` or legacy map."""
    result: dict[str, str] = {}
    agents = cfg.get("agents")
    if isinstance(agents, dict):
        for agent in INJECT_VIA_AGENT_NAMES:
            mode = agent_tools_inject_via(cfg, agent)
            if mode is not None:
                result[agent] = mode
        if result:
            return result
    pruning = cfg.get("pruning")
    if isinstance(pruning, dict):
        raw = pruning.get("inject_via")
        if isinstance(raw, dict):
            for agent, value in raw.items():
                agent_key = str(agent).strip()
                if agent_key not in INJECT_VIA_AGENT_NAMES:
                    continue
                mode = str(value).strip().lower()
                if mode in {"hook", "proxy"}:
                    result[agent_key] = mode
    return result


def inject_via_default_mode(cfg: dict[str, Any]) -> str:
    """Fallback inject mode when an agent has no explicit setting."""
    defaults = cfg.get("defaults")
    if isinstance(defaults, dict):
        value = defaults.get("inject_via_default")
        if value is not None:
            return str(value).strip().lower()
    pruning = cfg.get("pruning")
    if isinstance(pruning, dict):
        value = pruning.get("inject_via_default")
        if value is not None:
            return str(value).strip().lower()
    return "proxy"


def cyt_mcp_agent_from_config(cfg: dict[str, Any]) -> str | None:
    """Default cyt-mcp harness agent (``defaults.cyt_mcp_agent`` or legacy hook path)."""
    defaults = cfg.get("defaults")
    if isinstance(defaults, dict):
        value = defaults.get("cyt_mcp_agent")
        if value is not None:
            text = str(value).strip()
            if text:
                return text
    legacy = tools_at(cfg, "hook", "cyt_mcp", "agent")
    if legacy is not None:
        text = str(legacy).strip()
        if text:
            return text
    return None


def hallucination_gate_enabled_raw(
    cfg: dict[str, Any],
    *,
    overlay: dict[str, Any] | None = None,
) -> object | None:
    """Hallucination gate flag from overlay, ``defaults.hallucination_gate``, or legacy top-level key."""
    overlay_cfg = overlay if overlay is not None else cfg

    def _read_gate(source: dict[str, Any]) -> object | None:
        gate = source.get("hallucination_gate")
        if isinstance(gate, dict) and "enabled" in gate:
            return gate.get("enabled")
        defaults = source.get("defaults")
        if isinstance(defaults, dict):
            nested = defaults.get("hallucination_gate")
            if isinstance(nested, dict) and "enabled" in nested:
                return nested.get("enabled")
        return None

    overlay_value = _read_gate(overlay_cfg)
    if overlay_value is not None:
        return overlay_value
    return _read_gate(cfg)


def cursor_rule_file_enabled_raw(
    cfg: dict[str, Any],
    *,
    overlay: dict[str, Any] | None = None,
) -> object | None:
    """Cursor rule-file hook flag from ``agents.cursor.hook`` or legacy ``skills.hook``."""

    def _read_rule(source: dict[str, Any]) -> object | None:
        agents = source.get("agents")
        if isinstance(agents, dict):
            cursor = agents.get("cursor")
            if isinstance(cursor, dict):
                hook = cursor.get("hook")
                if isinstance(hook, dict):
                    rule = hook.get("cursor_rule_file")
                    if isinstance(rule, dict) and "enabled" in rule:
                        return rule.get("enabled")
        skills = source.get("skills")
        if isinstance(skills, dict):
            hook = skills.get("hook")
            if isinstance(hook, dict):
                rule = hook.get("cursor_rule_file")
                if isinstance(rule, dict) and "enabled" in rule:
                    return rule.get("enabled")
        return None

    overlay_cfg = overlay if overlay is not None else cfg
    overlay_value = _read_rule(overlay_cfg)
    if overlay_value is not None:
        return overlay_value
    return _read_rule(cfg)


def max_batch_workers_raw(
    cfg: dict[str, Any],
    *,
    overlay: dict[str, Any] | None = None,
) -> object | None:
    """Parallel prune workers from overlay, ``tools.max_batch_workers``, or legacy ``pruning`` key."""
    overlay_cfg = overlay if overlay is not None else cfg
    overlay_tools = overlay_cfg.get("tools")
    if isinstance(overlay_tools, dict) and overlay_tools.get("max_batch_workers") is not None:
        return overlay_tools.get("max_batch_workers")
    overlay_pruning = overlay_cfg.get("pruning")
    if isinstance(overlay_pruning, dict) and overlay_pruning.get("max_batch_workers") is not None:
        return overlay_pruning.get("max_batch_workers")
    value = tools_at(cfg, "max_batch_workers")
    if value is not None:
        return value
    pruning = cfg.get("pruning")
    if isinstance(pruning, dict):
        return pruning.get("max_batch_workers")
    return None


def tools_enabled_raw(
    cfg: dict[str, Any],
    *,
    overlay: dict[str, Any] | None = None,
) -> object | None:
    """Tools enabled flag from overlay, merged ``tools.enabled``, or legacy ``pruning.tools.enabled``."""
    overlay_cfg = overlay if overlay is not None else cfg
    overlay_tools = overlay_cfg.get("tools")
    if isinstance(overlay_tools, dict) and "enabled" in overlay_tools:
        return overlay_tools.get("enabled")
    overlay_pruning = overlay_cfg.get("pruning")
    if isinstance(overlay_pruning, dict):
        legacy_tools = overlay_pruning.get("tools")
        if isinstance(legacy_tools, dict) and "enabled" in legacy_tools:
            return legacy_tools.get("enabled")
    return tools_at(cfg, "enabled")


def build_agents_inject_via_overlay(inject_map: dict[str, str]) -> dict[str, Any]:
    """Build canonical ``agents.<agent>.tools.inject_via`` user-config overlay."""
    agents: dict[str, Any] = {}
    for agent, mode in inject_map.items():
        agent_key = str(agent).strip()
        if agent_key not in INJECT_VIA_AGENT_NAMES:
            continue
        normalized = str(mode).strip().lower()
        if normalized not in {"hook", "proxy"}:
            continue
        agents[agent_key] = {"tools": {"inject_via": normalized}}
    return {"agents": agents} if agents else {}


def build_defaults_hallucination_gate_overlay(*, enabled: bool) -> dict[str, Any]:
    """Build canonical ``defaults.hallucination_gate.enabled`` overlay."""
    return {"defaults": {"hallucination_gate": {"enabled": enabled}}}


def build_cursor_rule_file_overlay(*, enabled: bool) -> dict[str, Any]:
    """Build canonical ``agents.cursor.hook.cursor_rule_file.enabled`` overlay."""
    return {"agents": {"cursor": {"hook": {"cursor_rule_file": {"enabled": enabled}}}}}


def build_tools_root_overlay(tools: dict[str, Any]) -> dict[str, Any]:
    """Build canonical root ``tools`` overlay."""
    return {"tools": tools}
