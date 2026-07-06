"""Resolve launch endpoint names from config and CLI overrides."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cyt.launch.config import launch_endpoint_override
from cyt.launch.upstream import (
    AgentName,
    list_upstreams,
    select_upstream_endpoint,
)


def resolve_agent_endpoint(
    config: dict[str, Any],
    *,
    agent: AgentName,
    config_path: Path | None,
    endpoint_override: str | None,
    upstream_cli_endpoint: str | None,
) -> str:
    """Choose the reverse-proxy endpoint for this launch."""
    if agent == "cursor":
        return "cursor"

    if upstream_cli_endpoint is not None:
        return upstream_cli_endpoint

    if endpoint_override is not None:
        return endpoint_override.strip()

    if configured := launch_endpoint_override(config, agent):
        return configured

    return select_upstream_endpoint(
        list_upstreams(config),
        agent=agent,
        label="Select upstream endpoint for this launch",
    )
