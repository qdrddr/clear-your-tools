"""In-memory runtime catalog cache (process-local; no disk snapshots)."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RuntimeToolCache:
    """Full tool definitions keyed by agent-visible FastMCP names."""

    tools: list[dict[str, Any]] = field(default_factory=list)
    degraded_servers: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def replace(
        self,
        tools: list[dict[str, Any]],
        *,
        degraded_servers: list[str] | None = None,
    ) -> None:
        with self._lock:
            self.tools = list(tools)
            if degraded_servers is not None:
                self.degraded_servers = list(degraded_servers)

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self.tools]

    def degraded(self) -> list[str]:
        with self._lock:
            return list(self.degraded_servers)


GLOBAL_TOOL_CACHE = RuntimeToolCache()
