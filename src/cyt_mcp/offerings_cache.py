"""Cached MCP prompts/resources for non-blocking listOfferingsForUI."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from mcp.types import Prompt, Resource, ResourceTemplate

logger = logging.getLogger(__name__)

_tools_list_depth = 0

CoroFactory = Callable[[], Awaitable[None]]
EnsureMountedFn = Callable[[dict[str, Any]], list[str]]
OfferingsServer = Any


def enter_tools_list() -> None:
    global _tools_list_depth
    _tools_list_depth += 1


def exit_tools_list() -> None:
    global _tools_list_depth
    _tools_list_depth = max(0, _tools_list_depth - 1)


async def _wait_for_tools_list_idle(*, max_wait_s: float = 5.0) -> bool:
    """Yield until no tools/list handler is active (brief priority window)."""
    deadline = asyncio.get_event_loop().time() + max_wait_s
    while _tools_list_depth > 0:
        if asyncio.get_event_loop().time() >= deadline:
            return False
        await asyncio.sleep(0.02)
    return True


@dataclass
class OfferingsSnapshot:
    resources: list[Any] = field(default_factory=list)
    prompts: list[Any] = field(default_factory=list)
    resource_templates: list[Any] = field(default_factory=list)

    @property
    def total_count(self) -> int:
        return len(self.resources) + len(self.prompts) + len(self.resource_templates)

    def to_json(self) -> dict[str, Any]:
        from cyt_mcp.catalog_build import json_safe_value

        return {
            "resources": [json_safe_value(item) for item in self.resources],
            "prompts": [json_safe_value(item) for item in self.prompts],
            "resource_templates": [json_safe_value(item) for item in self.resource_templates],
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> OfferingsSnapshot:
        return cls(
            resources=list(payload.get("resources") or []),
            prompts=list(payload.get("prompts") or []),
            resource_templates=list(payload.get("resource_templates") or []),
        )


def normalize_offering_items(items: Sequence[Any] | None) -> list[dict[str, Any]]:
    from cyt_mcp.catalog_build import json_safe_value

    return [cast(dict[str, Any], json_safe_value(item)) for item in (items or [])]


def offerings_snapshot_from_server(
    *,
    resources: Sequence[Any] | None,
    prompts: Sequence[Any] | None,
    resource_templates: Sequence[Any] | None,
) -> OfferingsSnapshot:
    return OfferingsSnapshot(
        resources=normalize_offering_items(resources),
        prompts=normalize_offering_items(prompts),
        resource_templates=normalize_offering_items(resource_templates),
    )


def offerings_to_wire[TWire: Resource | Prompt | ResourceTemplate](
    items: Sequence[Any],
    wire_type: type[TWire],
) -> list[TWire]:
    wired: list[TWire] = []
    for item in items:
        if isinstance(item, wire_type):
            wired.append(item)
        elif isinstance(item, dict):
            validated = wire_type.model_validate(item)
            assert isinstance(validated, wire_type)
            wired.append(validated)
        else:
            from cyt_mcp.catalog_build import json_safe_value

            validated = wire_type.model_validate(json_safe_value(item))
            assert isinstance(validated, wire_type)
            wired.append(validated)
    return wired


def _offerings_disk_path(slug: str) -> Path:
    from cyt.cyt_mcp.catalog_disk import cyt_mcp_catalog_cache_dir

    return cyt_mcp_catalog_cache_dir() / "offerings" / f"{slug}.json"


def persist_offerings_snapshot(slug: str, snapshot: OfferingsSnapshot) -> bool:
    if not slug or snapshot.total_count == 0:
        return False
    path = _offerings_disk_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(json.dumps(snapshot.to_json(), ensure_ascii=False), encoding="utf-8")
        return True
    except (OSError, TypeError, ValueError) as exc:
        logger.debug("cyt-mcp offerings disk persist failed: %s", exc)
        return False


def hydrate_offerings_snapshot(slug: str) -> OfferingsSnapshot | None:
    path = _offerings_disk_path(slug)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    snapshot = OfferingsSnapshot.from_json(payload)
    return snapshot if snapshot.total_count > 0 else None


class OfferingsCache:
    """Per-workspace snapshots so list_resources/prompts never block tools/list."""

    def __init__(self) -> None:
        self._snapshots: dict[str, OfferingsSnapshot] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._refresh_tasks: dict[str, asyncio.Task[None]] = {}

    def get(self, runtime_key: str) -> OfferingsSnapshot | None:
        return self._snapshots.get(runtime_key)

    def snapshot_or_empty(self, runtime_key: str) -> OfferingsSnapshot:
        return self._snapshots.get(runtime_key) or OfferingsSnapshot()

    def replace(self, runtime_key: str, snapshot: OfferingsSnapshot) -> OfferingsSnapshot:
        existing = self._snapshots.get(runtime_key)
        if existing is not None and existing.total_count > 0 and snapshot.total_count == 0:
            return existing
        self._snapshots[runtime_key] = snapshot
        return snapshot

    def _lock_for(self, runtime_key: str) -> asyncio.Lock:
        lock = self._locks.get(runtime_key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[runtime_key] = lock
        return lock

    def schedule_refresh_once(
        self,
        *,
        runtime_key: str,
        coro_factory: CoroFactory,
        delay_s: float = 15.0,
    ) -> None:
        existing = self._refresh_tasks.get(runtime_key)
        if existing is not None and not existing.done():
            return

        async def _run() -> None:
            try:
                if delay_s > 0:
                    await asyncio.sleep(delay_s)
                await coro_factory()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("cyt-mcp offerings refresh failed: %s", exc)

        self._refresh_tasks[runtime_key] = asyncio.create_task(
            _run(),
            name=f"cyt-mcp-offerings-{runtime_key}",
        )

    async def refresh_from_server(
        self,
        server: OfferingsServer,
        *,
        runtime_key: str,
        mcp_servers: dict[str, Any],
        ensure_mounted: EnsureMountedFn,
        disk_slug: str | None = None,
    ) -> OfferingsSnapshot:
        """Fetch offerings on the main event loop (never a secondary event loop)."""
        async with self._lock_for(runtime_key):
            if not await _wait_for_tools_list_idle():
                return self.snapshot_or_empty(runtime_key)

            await asyncio.to_thread(ensure_mounted, mcp_servers)
            resources = await server._list_resources()
            prompts = await server._list_prompts()
            templates = await server._list_resource_templates()
            snapshot = offerings_snapshot_from_server(
                resources=resources,
                prompts=prompts,
                resource_templates=templates,
            )
            stored = self.replace(runtime_key, snapshot)
            if disk_slug and stored.total_count > 0:
                persist_offerings_snapshot(disk_slug, stored)
            return stored
