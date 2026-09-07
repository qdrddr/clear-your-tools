"""On-disk mcp-config.yaml migration (rename + stub catalog)."""

from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Any

import yaml

from cyt_mcp.stub_catalog import (
    DEFAULT_STUB_BY_AGENT,
    DEFAULT_STUB_NAME,
    DEFAULT_STUBS,
)

logger = logging.getLogger(__name__)

LEGACY_MCP_CONFIG_NAME = "mcp-aggregator.yaml"
MCP_CONFIG_NAME = "mcp-config.yaml"


def _load_yaml_dict(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        loaded = yaml.safe_load(f)
    return loaded if isinstance(loaded, dict) else {}


def _write_yaml_dict(path: Path, cfg: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(cfg, default_flow_style=False, sort_keys=False), encoding="utf-8")


def _ensure_tools_block(cfg: dict[str, Any]) -> dict[str, Any]:
    pruning = cfg.get("pruning")
    if not isinstance(pruning, dict):
        pruning = {}
        cfg["pruning"] = pruning
    tools = pruning.get("tools")
    if not isinstance(tools, dict):
        tools = {}
        pruning["tools"] = tools
    return tools


def upgrade_mcp_config_dict(cfg: dict[str, Any]) -> dict[str, Any]:
    """Migrate legacy aggregator keys to stub catalog shape in-memory."""
    result = copy.deepcopy(cfg)

    codex_flag = result.pop("codex_stubs_include_description", None)
    tools = _ensure_tools_block(result)

    if "stubs" not in tools:
        tools["stubs"] = copy.deepcopy(DEFAULT_STUBS)
    if "stub" not in tools:
        tools["stub"] = DEFAULT_STUB_NAME

    by_agent = tools.get("stub_by_agent")
    if not isinstance(by_agent, dict):
        by_agent = {}
        tools["stub_by_agent"] = by_agent

    merged_by_agent = dict(DEFAULT_STUB_BY_AGENT)
    merged_by_agent.update({str(k): str(v) for k, v in by_agent.items() if str(k).strip() and str(v).strip()})

    if codex_flag is not None:
        merged_by_agent["codex"] = "codex" if bool(codex_flag) else "basic"

    for agent, stub in DEFAULT_STUB_BY_AGENT.items():
        merged_by_agent.setdefault(agent, stub)
    tools["stub_by_agent"] = merged_by_agent

    return result


def promote_legacy_mcp_config_path(path: Path) -> Path:
    """Rename ``mcp-aggregator.yaml`` beside *path* to ``mcp-config.yaml`` when needed."""
    resolved = path.expanduser()
    if resolved.name == MCP_CONFIG_NAME:
        return resolved
    if resolved.name == LEGACY_MCP_CONFIG_NAME:
        target = resolved.with_name(MCP_CONFIG_NAME)
        if not target.exists():
            resolved.rename(target)
            logger.info("Renamed MCP config %s -> %s", resolved, target)
            return target
        return target
    return resolved


def resolve_mcp_config_path(path: Path | None, *, default: Path) -> Path:
    """Resolve MCP config path, preferring canonical name then legacy."""
    candidate = (path or default).expanduser()
    if candidate.is_file():
        return promote_legacy_mcp_config_path(candidate)
    if candidate.name == MCP_CONFIG_NAME:
        legacy = candidate.with_name(LEGACY_MCP_CONFIG_NAME)
        if legacy.is_file():
            return promote_legacy_mcp_config_path(legacy)
    if candidate.name == LEGACY_MCP_CONFIG_NAME:
        canonical = candidate.with_name(MCP_CONFIG_NAME)
        if canonical.is_file():
            return canonical
        if candidate.is_file():
            return promote_legacy_mcp_config_path(candidate)
    return candidate


def migrate_mcp_config_file(path: Path, *, dry_run: bool = False) -> dict[str, Any] | None:
    """Upgrade on-disk MCP config; returns migrated dict or None if missing."""
    resolved = resolve_mcp_config_path(path, default=path)
    if not resolved.is_file():
        return None

    raw = _load_yaml_dict(resolved)
    upgraded = upgrade_mcp_config_dict(raw)
    if upgraded == raw and resolved.name == MCP_CONFIG_NAME:
        return upgraded

    if dry_run:
        return upgraded

    target = promote_legacy_mcp_config_path(resolved)
    _write_yaml_dict(target, upgraded)
    return upgraded


def maybe_migrate_mcp_config_file(path: Path) -> dict[str, Any] | None:
    """Auto-migrate MCP config when legacy keys or filename are present."""
    resolved = resolve_mcp_config_path(path, default=path)
    if not resolved.is_file():
        legacy = resolved.with_name(LEGACY_MCP_CONFIG_NAME)
        if legacy.is_file():
            resolved = promote_legacy_mcp_config_path(legacy)
        else:
            return None

    raw = _load_yaml_dict(resolved)
    needs_upgrade = (
        "codex_stubs_include_description" in raw
        or resolved.name == LEGACY_MCP_CONFIG_NAME
        or not isinstance(_ensure_tools_block(copy.deepcopy(raw)).get("stubs"), list)
    )
    if not needs_upgrade:
        return None
    return migrate_mcp_config_file(resolved, dry_run=False)
