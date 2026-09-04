"""Workspace-aware CYT config resolution for hook daemon requests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cyt.config import DEFAULT_USER_CONFIG_PATH, deep_merge, load_config
from cyt.hook.install_scope import CytInstallScope, HookAgentName
from cyt_client.rules_file import workspace_root_from_payload

HOOK_WORKSPACE_CONFIG_KEY = "_cyt_hook_workspace_root"


def _load_yaml_dict(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    import yaml

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def resolve_hook_request_config(
    payload: dict[str, Any],
    agent: str,
    *,
    base_config: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], Path | None]:
    """Deep-merge global CYT config + ``.agents/cyt/config/config.yaml`` when workspace present."""
    workspace = workspace_root_from_payload(payload)
    global_config = (
        base_config if base_config is not None else load_config(DEFAULT_USER_CONFIG_PATH)
    )
    if workspace is None:
        return global_config, None

    scope = CytInstallScope(workspace_root=workspace)
    agent_name: HookAgentName
    normalized = (agent or "cursor").strip().lower()
    if normalized not in {"cursor", "claude", "codex"}:
        normalized = "cursor"
    agent_name = normalized  # type: ignore[assignment]

    workspace_config_path = scope.resolve_workspace_cyt_config_path(agent_name)
    if workspace_config_path is None or not workspace_config_path.is_file():
        return global_config, workspace

    workspace_overlay = _load_yaml_dict(workspace_config_path)
    if not workspace_overlay:
        return global_config, workspace

    merged = deep_merge(global_config, workspace_overlay)
    return merged, workspace


def set_hook_workspace_in_config(
    config: dict[str, Any],
    workspace: Path | None,
) -> dict[str, Any]:
    """Attach hook workspace root for cyt-mcp registry merge lookups."""
    import copy

    merged = copy.deepcopy(config)
    if workspace is None:
        merged.pop(HOOK_WORKSPACE_CONFIG_KEY, None)
        return merged
    merged[HOOK_WORKSPACE_CONFIG_KEY] = str(workspace)
    return merged


def hook_workspace_from_config(config: dict[str, Any] | None) -> Path | None:
    if not config:
        return None
    raw = config.get(HOOK_WORKSPACE_CONFIG_KEY)
    if not isinstance(raw, str) or not raw.strip():
        return None
    path = Path(raw.strip()).expanduser()
    try:
        return path.resolve() if path.is_dir() else None
    except OSError:
        return None
