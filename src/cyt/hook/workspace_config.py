"""Workspace-aware CYT config resolution for hook daemon requests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cyt.config import DEFAULT_USER_CONFIG_PATH, deep_merge, load_config
from cyt.hook.install_scope import CytInstallScope, HookAgentName
from cyt_client.rules_file import workspace_root_from_payload


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
    """Deep-merge global CYT config + ``.<agent>/cyt/config/config.yaml`` when workspace present."""
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


def with_dynamic_catalog_url(
    config: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Return a config copy whose cyt-mcp catalog URL includes workspace when present."""
    import copy

    catalog_url = hook_catalog_url_for_payload(config, payload)
    if not catalog_url:
        return config
    merged = copy.deepcopy(config)
    pruning = merged.get("pruning")
    if not isinstance(pruning, dict):
        pruning = {}
        merged["pruning"] = pruning
    tools = pruning.get("tools")
    if not isinstance(tools, dict):
        tools = {}
        pruning["tools"] = tools
    hook = tools.get("hook")
    if not isinstance(hook, dict):
        hook = {}
        tools["hook"] = hook
    cyt_mcp = hook.get("cyt_mcp")
    if not isinstance(cyt_mcp, dict):
        cyt_mcp = {}
        hook["cyt_mcp"] = cyt_mcp
    cyt_mcp["catalog_url"] = catalog_url
    return merged


def hook_catalog_url_for_payload(
    config: dict[str, Any],
    payload: dict[str, Any],
) -> str:
    """Build cyt-mcp catalog URL, adding ``workspace`` query when payload includes a root."""
    from cyt.config import tools_hook_cyt_mcp_catalog_url
    from cyt_client.mcp_entry import cyt_mcp_http_catalog_url, load_aggregator_transport_settings

    base_url = tools_hook_cyt_mcp_catalog_url(config)
    if not base_url:
        transport, host, port, _mcp_path, catalog_path = load_aggregator_transport_settings()
        if transport != "http":
            return ""
        base_url = cyt_mcp_http_catalog_url(host=host, port=port, catalog_path=catalog_path)

    workspace = workspace_root_from_payload(payload)
    if workspace is None:
        return base_url

    transport, host, port, _mcp_path, catalog_path = load_aggregator_transport_settings()
    return cyt_mcp_http_catalog_url(
        host=host,
        port=port,
        catalog_path=catalog_path,
        workspace_root=str(workspace),
    )
