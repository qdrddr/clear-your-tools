"""Connection health cache for executor hook injection."""

from __future__ import annotations

import asyncio
import copy
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_CONNECTIONS_PATH = "/api/connections"
_HEALTHY_STATUS = "healthy"
_DEFAULT_PROBE_CONCURRENCY = 4
_TOOL_METADATA_KEYS = ("owner", "integration", "connection", "static")

_health_lock = threading.Lock()
_health_states: dict[str, ConnectionHealthSnapshot] = {}
_permissive_filter_logged: set[str] = set()


@dataclass(frozen=True)
class ConnectionKey:
    owner: str
    integration: str
    name: str


@dataclass
class ConnectionHealthSnapshot:
    connections: list[dict[str, Any]] = field(default_factory=list)
    healthy_integrations: set[tuple[str, str]] = field(default_factory=set)
    updated_at: float = 0.0
    loaded: bool = False


def clear_connection_health_cache() -> None:
    """Reset in-process connection health state (for tests)."""
    with _health_lock:
        _health_states.clear()
        _permissive_filter_logged.clear()


def build_healthy_integrations(connections: list[dict[str, Any]]) -> set[tuple[str, str]]:
    """Return ``(owner, integration)`` pairs with at least one healthy connection."""
    by_integration: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for conn in connections:
        owner = str(conn.get("owner") or "").strip()
        integration = str(conn.get("integration") or "").strip()
        if not owner or not integration:
            continue
        key = (owner, integration)
        by_integration.setdefault(key, []).append(conn)

    healthy: set[tuple[str, str]] = set()
    for key, group in by_integration.items():
        if any(_connection_is_healthy(conn) for conn in group):
            healthy.add(key)
    return healthy


def _connection_is_healthy(conn: dict[str, Any]) -> bool:
    last_health = conn.get("lastHealth")
    if not isinstance(last_health, dict):
        return False
    return last_health.get("status") == _HEALTHY_STATUS


def connection_key_from_dict(conn: dict[str, Any]) -> ConnectionKey | None:
    owner = str(conn.get("owner") or "").strip()
    integration = str(conn.get("integration") or "").strip()
    name = str(conn.get("name") or "").strip()
    if not owner or not integration or not name:
        return None
    return ConnectionKey(owner=owner, integration=integration, name=name)


def _connection_needs_probe(conn: dict[str, Any]) -> bool:
    last_health = conn.get("lastHealth")
    if last_health is None:
        return True
    if not isinstance(last_health, dict):
        return True
    return last_health.get("status") != _HEALTHY_STATUS


def _tool_is_exempt_from_health_gate(tool: dict[str, Any]) -> bool:
    integration = str(tool.get("integration") or "").strip()
    if integration == "executor":
        return True
    return tool.get("static") is True


def filter_tools_by_integration_health(
    tools: list[dict[str, Any]],
    healthy_integrations: set[tuple[str, str]] | None,
    *,
    apply_filter: bool = True,
) -> list[dict[str, Any]]:
    """Drop tools whose integration has no healthy connection."""
    if not apply_filter or healthy_integrations is None:
        return tools

    filtered: list[dict[str, Any]] = []
    for tool in tools:
        if _tool_is_exempt_from_health_gate(tool):
            filtered.append(tool)
            continue
        owner = str(tool.get("owner") or "").strip()
        integration = str(tool.get("integration") or "").strip()
        if not owner or not integration:
            logger.debug(
                "excluding tool %s from health gate: missing owner/integration",
                tool.get("name"),
            )
            continue
        if (owner, integration) in healthy_integrations:
            filtered.append(tool)
    return filtered


def health_snapshot_to_disk(snapshot: ConnectionHealthSnapshot) -> dict[str, Any]:
    return {
        "connections": copy.deepcopy(snapshot.connections),
        "healthy_integrations": [list(pair) for pair in sorted(snapshot.healthy_integrations)],
        "updated_at": datetime.now(tz=UTC).isoformat(),
    }


def health_snapshot_from_disk(payload: dict[str, Any]) -> ConnectionHealthSnapshot | None:
    connections = payload.get("connections")
    if not isinstance(connections, list):
        return None

    healthy: set[tuple[str, str]] = set()
    healthy_raw = payload.get("healthy_integrations")
    if isinstance(healthy_raw, list):
        for item in healthy_raw:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                healthy.add((str(item[0]), str(item[1])))
    if not healthy:
        healthy = build_healthy_integrations(connections)

    return ConnectionHealthSnapshot(
        connections=copy.deepcopy(connections),
        healthy_integrations=healthy,
        updated_at=time.monotonic(),
        loaded=True,
    )


def apply_health_snapshot(slug: str, snapshot: ConnectionHealthSnapshot) -> None:
    with _health_lock:
        snapshot.loaded = True
        _health_states[slug] = snapshot


def load_health_snapshot_from_disk(slug: str, payload: dict[str, Any]) -> bool:
    snapshot = health_snapshot_from_disk(payload)
    if snapshot is None:
        return False
    apply_health_snapshot(slug, snapshot)
    return True


def snapshot_health_for_catalog(slug: str) -> ConnectionHealthSnapshot | None:
    with _health_lock:
        state = _health_states.get(slug)
        if state is None:
            return None
        return ConnectionHealthSnapshot(
            connections=copy.deepcopy(state.connections),
            healthy_integrations=set(state.healthy_integrations),
            updated_at=state.updated_at,
            loaded=state.loaded,
        )


def health_cache_loaded(slug: str) -> bool:
    with _health_lock:
        state = _health_states.get(slug)
        return state is not None and state.loaded


def filter_catalog_by_health(
    tools: list[dict[str, Any]],
    slug: str,
) -> list[dict[str, Any]]:
    """Apply integration health gate when cache is loaded; permissive until first refresh."""
    snapshot = snapshot_health_for_catalog(slug)
    if snapshot is None or not snapshot.loaded:
        _log_permissive_filter_once(slug)
        return tools
    return filter_tools_by_integration_health(tools, snapshot.healthy_integrations)


def _log_permissive_filter_once(slug: str) -> None:
    with _health_lock:
        if slug in _permissive_filter_logged:
            return
        _permissive_filter_logged.add(slug)
    logger.info(
        "executor connection health cache empty; returning unfiltered catalog until refresh completes slug=%s",
        slug,
    )


def connection_health_snapshot_fields(slug: str) -> dict[str, Any]:
    """Fields for ``executor_catalog_health_snapshot``."""
    snapshot = snapshot_health_for_catalog(slug)
    if snapshot is None:
        return {
            "connection_health_loaded": False,
            "healthy_integration_count": 0,
            "total_connection_count": 0,
        }

    age_seconds = time.monotonic() - snapshot.updated_at if snapshot.updated_at else None
    payload: dict[str, Any] = {
        "connection_health_loaded": snapshot.loaded,
        "healthy_integration_count": len(snapshot.healthy_integrations),
        "total_connection_count": len(snapshot.connections),
    }
    if age_seconds is not None:
        payload["health_cache_age_seconds"] = round(age_seconds, 1)
    return payload


def _auth_headers(token: str | None) -> dict[str, str]:
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


async def fetch_connections_async(
    *,
    base_url: str,
    token: str | None,
) -> list[dict[str, Any]]:
    headers = _auth_headers(token)
    timeout = httpx.Timeout(60.0, connect=10.0)
    async with httpx.AsyncClient(headers=headers, timeout=timeout) as client:
        response = await client.get(f"{base_url.rstrip('/')}{_CONNECTIONS_PATH}")
        response.raise_for_status()
        listed = response.json()
    if not isinstance(listed, list):
        raise ValueError("executor /api/connections response must be a JSON array")
    return [item for item in listed if isinstance(item, dict)]


async def probe_connection_health_async(
    client: httpx.AsyncClient,
    key: ConnectionKey,
) -> dict[str, Any] | None:
    path = f"/api/connections/{key.owner}/{key.integration}/{key.name}/health"
    try:
        response = await client.post(path)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning(
            "executor connection health probe failed for %s/%s/%s: %s",
            key.owner,
            key.integration,
            key.name,
            exc,
        )
        return None

    payload = response.json()
    return payload if isinstance(payload, dict) else None


async def refresh_connection_health_async(
    *,
    base_url: str,
    token: str | None,
    concurrency: int = _DEFAULT_PROBE_CONCURRENCY,
) -> ConnectionHealthSnapshot:
    """Fetch connections and probe unhealthy or unchecked connections in the background."""
    connections = await fetch_connections_async(base_url=base_url, token=token)
    connections_copy = copy.deepcopy(connections)
    needs_probe = [
        conn
        for conn in connections_copy
        if isinstance(conn, dict)
        and connection_key_from_dict(conn)
        and _connection_needs_probe(conn)
    ]

    if needs_probe:
        headers = _auth_headers(token)
        timeout = httpx.Timeout(60.0, connect=10.0)
        semaphore = asyncio.Semaphore(max(1, concurrency))
        async with httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=timeout,
        ) as client:

            async def probe_one(conn: dict[str, Any]) -> None:
                key = connection_key_from_dict(conn)
                if key is None:
                    return
                async with semaphore:
                    result = await probe_connection_health_async(client, key)
                if result is None:
                    return
                last_health: dict[str, Any] = {
                    "status": result.get("status"),
                    "checkedAt": result.get("checkedAt"),
                }
                detail = result.get("detail")
                if detail is not None:
                    last_health["detail"] = detail
                conn["lastHealth"] = last_health

            await asyncio.gather(*(probe_one(conn) for conn in needs_probe))

    healthy = build_healthy_integrations(connections_copy)
    logger.info(
        "executor connection health refreshed connections=%d healthy_integrations=%d",
        len(connections_copy),
        len(healthy),
    )
    return ConnectionHealthSnapshot(
        connections=connections_copy,
        healthy_integrations=healthy,
        updated_at=time.monotonic(),
        loaded=True,
    )


def merge_tool_metadata(tool: dict[str, Any], metadata: dict[str, Any] | None) -> None:
    """Attach routing metadata from the tools list onto a normalized tool dict."""
    if not metadata:
        return
    for key in _TOOL_METADATA_KEYS:
        if key in metadata and metadata[key] is not None:
            tool[key] = metadata[key]
