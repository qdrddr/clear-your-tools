"""In-memory runtime catalog cache (process-local; no disk snapshots)."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from fastmcp.tools.tool import Tool


@dataclass
class RuntimeToolCache:
    """Hook-daemon catalog (full backend defs) and search_index for cyt-mcp_search."""

    tools: list[dict[str, Any]] = field(default_factory=list)
    search_index: dict[str, dict[str, Any]] = field(default_factory=dict)
    degraded_servers: list[str] = field(default_factory=list)
    _search_tool: Tool | None = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def replace(
        self,
        tools: list[dict[str, Any]],
        *,
        search_index: dict[str, dict[str, Any]] | None = None,
        degraded_servers: list[str] | None = None,
    ) -> None:
        with self._lock:
            self.tools = list(tools)
            if search_index is not None:
                self.search_index = {key: dict(value) for key, value in search_index.items()}
            if degraded_servers is not None:
                self.degraded_servers = list(degraded_servers)

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self.tools]

    def search_index_snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {key: dict(value) for key, value in self.search_index.items()}

    def search_index_entry(self, name: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self.search_index.get(name)
            return dict(entry) if entry is not None else None

    def degraded(self) -> list[str]:
        with self._lock:
            return list(self.degraded_servers)

    def set_search_tool(self, tool: Tool | None) -> None:
        with self._lock:
            self._search_tool = tool

    def search_tool(self) -> Tool | None:
        with self._lock:
            return self._search_tool
