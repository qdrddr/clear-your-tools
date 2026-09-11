"""Mutable holder for runtime cyt-mcp aggregator config."""

from __future__ import annotations

from dataclasses import dataclass, replace

from cyt_mcp.config import AggregatorConfig, reload_mcp_deny


@dataclass
class ConfigHolder:
    """Wrap :class:`AggregatorConfig` so ``mcp_deny`` can reload without process restart."""

    config: AggregatorConfig

    @property
    def mcp_deny(self) -> tuple[str, ...]:
        return self.config.mcp_deny

    def reload_mcp_deny(self) -> tuple[str, ...]:
        updated = reload_mcp_deny(self.config)
        self.config = replace(self.config, mcp_deny=updated)
        return updated
