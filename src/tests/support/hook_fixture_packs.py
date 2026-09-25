"""Shared structural types for hook-related test fixture packs."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class HookWorkspacePack(Protocol):
    @property
    def workspace(self) -> Path: ...

    @property
    def global_config_path(self) -> Path: ...


class HookCatalogPack(HookWorkspacePack, Protocol):
    @property
    def catalog_cache_dir(self) -> Path: ...

    @property
    def global_mcp_agg(self) -> Path: ...

    @property
    def global_mcp_defs(self) -> Path: ...

    @property
    def tools(self) -> list[dict[str, Any]]: ...
