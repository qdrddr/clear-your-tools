"""Connection health cache for executor hook injection."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from cyt.executor.connection_flapping import (
    clear_flapping_cache,
    flapping_policy_from_config,
    flapping_snapshot_fields,
    flapping_state_to_disk,
    gated_connections,
    load_flapping_state_from_disk,
    update_flapping_states,
)
from cyt.executor.runtime import (
    connection_health_flapping_settings,
    load_config,
    tools_hook_executor_cache_settings,
)

logger = logging.getLogger(__name__)

_CONNECTIONS_PATH = "/api/connections"
_HEALTHY_STATUS = "healthy"
_TOOL_METADATA_KEYS = ("owner", "integration", "connection", "static", "tool_name")

_health_lock = threading.Lock()
_health_states: dict[str, ConnectionHealthSnapshot] = {}
_permissive_filter_logged: set[str] = set()
_debug_disk_enabled = False


@dataclass(frozen=True)
class ConnectionKey:
    owner: str
    integration: str
    name: str


@dataclass
class ConnectionHealthSnapshot:
    connections: dict[ConnectionKey, dict[str, Any]] = field(default_factory=dict)
    healthy_connections: set[ConnectionKey] = field(default_factory=set)
    updated_at: float = 0.0
    loaded: bool = False


@dataclass(frozen=True)
class EligibilityDelta:
    newly_eligible: set[ConnectionKey]
    newly_ineligible: set[ConnectionKey]


def set_executor_debug_disk(enabled: bool) -> None:
    """When True, health/flapping blocks are written to disk on flush (debug only)."""
    global _debug_disk_enabled
    _debug_disk_enabled = enabled


def debug_disk_enabled() -> bool:
    return _debug_disk_enabled


def clear_connection_health_cache() -> None:
    """Reset in-process connection health state (for tests)."""
    with _health_lock:
        _health_states.clear()
        _permissive_filter_logged.clear()
    clear_flapping_cache()


def connection_key_from_dict(conn: dict[str, Any]) -> ConnectionKey | None:
    owner = str(conn.get("owner") or "").strip()
    integration = str(conn.get("integration") or "").strip()
    name = str(conn.get("name") or "").strip()
    if not owner or not integration or not name:
        return None
    return ConnectionKey(owner=owner, integration=integration, name=name)


def connection_key_from_tool(tool: dict[str, Any]) -> ConnectionKey | None:
    owner = str(tool.get("owner") or "").strip()
    integration = str(tool.get("integration") or "").strip()
    name = str(tool.get("connection") or "").strip()
    if not owner or not integration or not name:
        return None
    return ConnectionKey(owner=owner, integration=integration, name=name)


def connections_list_to_dict(
    connections: list[dict[str, Any]],
) -> dict[ConnectionKey, dict[str, Any]]:
    result: dict[ConnectionKey, dict[str, Any]] = {}
    for conn in connections:
        key = connection_key_from_dict(conn)
        if key is None:
            continue
        result[key] = conn
    return result


def build_healthy_connections(
    connections: dict[ConnectionKey, dict[str, Any]] | list[dict[str, Any]],
) -> set[ConnectionKey]:
    """Return connection keys whose ``lastHealth.status`` is healthy."""
    if isinstance(connections, list):
        connections = connections_list_to_dict(connections)
    healthy: set[ConnectionKey] = set()
    for key, conn in connections.items():
        if _connection_is_healthy(conn):
            healthy.add(key)
    return healthy


def connections_fingerprint(connections: dict[ConnectionKey, dict[str, Any]]) -> str:
    keys = sorted(f"{key.owner}/{key.integration}/{key.name}" for key in connections)
    payload = "\n".join(keys)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def eligible_connections(
    *,
    healthy_connections: set[ConnectionKey],
    gated_connections: set[ConnectionKey],
) -> set[ConnectionKey]:
    return healthy_connections - gated_connections


def tool_schema_eligible(
    tool_or_summary: dict[str, Any],
    *,
    healthy_connections: set[ConnectionKey] | None,
    gated_connections: set[ConnectionKey],
    permissive: bool = False,
) -> bool:
    if tool_or_summary.get("static") is True:
        return True
    if str(tool_or_summary.get("integration") or "") == "executor":
        return True
    key = connection_key_from_tool(tool_or_summary)
    if key is None:
        return False
    if key in gated_connections:
        return False
    if permissive or healthy_connections is None:
        return True
    return key in healthy_connections


def compute_eligibility_delta(
    *,
    previous_healthy: set[ConnectionKey],
    previous_gated: set[ConnectionKey],
    current_healthy: set[ConnectionKey],
    current_gated: set[ConnectionKey],
) -> EligibilityDelta:
    previous_eligible = eligible_connections(
        healthy_connections=previous_healthy,
        gated_connections=previous_gated,
    )
    current_eligible = eligible_connections(
        healthy_connections=current_healthy,
        gated_connections=current_gated,
    )
    return EligibilityDelta(
        newly_eligible=current_eligible - previous_eligible,
        newly_ineligible=previous_eligible - current_eligible,
    )


def _connection_is_healthy(conn: dict[str, Any]) -> bool:
    last_health = conn.get("lastHealth")
    if not isinstance(last_health, dict):
        return False
    return last_health.get("status") == _HEALTHY_STATUS


def _connection_key_to_disk(key: ConnectionKey) -> str:
    return f"{key.owner}/{key.integration}/{key.name}"


def _connection_key_from_disk(text: str) -> ConnectionKey | None:
    parts = text.split("/", 2)
    if len(parts) != 3:
        return None
    owner, integration, name = (part.strip() for part in parts)
    if not owner or not integration or not name:
        return None
    return ConnectionKey(owner=owner, integration=integration, name=name)


def filter_catalog_by_health(
    tools: list[dict[str, Any]],
    slug: str,
    *,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Apply connection health + flapping gate when cache is loaded; permissive until refresh."""
    snapshot = snapshot_health_for_catalog(slug)
    if snapshot is None or not snapshot.loaded:
        _log_permissive_filter_once(slug)
        return tools
    cfg = config or load_config()
    policy = flapping_policy_from_config(connection_health_flapping_settings(cfg))
    gated = gated_connections(slug, policy=policy)
    filtered: list[dict[str, Any]] = []
    for tool in tools:
        if tool_schema_eligible(
            tool,
            healthy_connections=snapshot.healthy_connections,
            gated_connections=gated,
        ):
            filtered.append(tool)
    return filtered


def filter_summaries_for_schema_fetch(
    summaries: list[tuple[str, str | None, dict[str, Any]]],
    slug: str,
    *,
    config: dict[str, Any] | None = None,
) -> list[tuple[str, str | None, dict[str, Any]]]:
    """Return summaries eligible for tier-2 schema fetch."""
    snapshot = snapshot_health_for_catalog(slug)
    cfg = config or load_config()
    policy = flapping_policy_from_config(connection_health_flapping_settings(cfg))
    gated = gated_connections(slug, policy=policy)
    permissive = snapshot is None or not snapshot.loaded
    healthy: set[ConnectionKey] | None
    if permissive:
        healthy = None
    elif snapshot is not None:
        healthy = snapshot.healthy_connections
    else:
        healthy = None
    return [
        summary
        for summary in summaries
        if tool_schema_eligible(
            summary[2],
            healthy_connections=healthy,
            gated_connections=gated,
            permissive=permissive,
        )
    ]


def _log_permissive_filter_once(slug: str) -> None:
    with _health_lock:
        if slug in _permissive_filter_logged:
            return
        _permissive_filter_logged.add(slug)
    logger.info(
        "executor connection health cache empty; returning unfiltered catalog until refresh completes slug=%s",
        slug,
    )


def health_snapshot_to_disk(
    snapshot: ConnectionHealthSnapshot,
    *,
    slug: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "connections": {
            _connection_key_to_disk(key): copy.deepcopy(conn)
            for key, conn in sorted(
                snapshot.connections.items(),
                key=lambda item: _connection_key_to_disk(item[0]),
            )
        },
        "healthy_connections": [
            [key.owner, key.integration, key.name]
            for key in sorted(
                snapshot.healthy_connections,
                key=lambda item: (item.owner, item.integration, item.name),
            )
        ],
        "updated_at": datetime.now(tz=UTC).isoformat(),
    }
    if slug is not None:
        policy = flapping_policy_from_config(connection_health_flapping_settings(config))
        flapping = flapping_state_to_disk(slug, policy=policy)
        if flapping:
            payload["flapping"] = flapping
    return payload


def health_snapshot_from_disk(payload: dict[str, Any]) -> ConnectionHealthSnapshot | None:
    connections_raw = payload.get("connections")
    if isinstance(connections_raw, list):
        connections = connections_list_to_dict(connections_raw)
    elif isinstance(connections_raw, dict):
        connections = {}
        for key_text, conn in connections_raw.items():
            if not isinstance(conn, dict):
                continue
            key = _connection_key_from_disk(str(key_text))
            if key is None:
                continue
            connections[key] = copy.deepcopy(conn)
    else:
        return None

    healthy: set[ConnectionKey] = set()
    healthy_raw = payload.get("healthy_connections")
    if isinstance(healthy_raw, list):
        for item in healthy_raw:
            if isinstance(item, (list, tuple)) and len(item) == 3:
                healthy.add(
                    ConnectionKey(
                        owner=str(item[0]),
                        integration=str(item[1]),
                        name=str(item[2]),
                    ),
                )
    if not healthy:
        healthy = build_healthy_connections(connections)

    return ConnectionHealthSnapshot(
        connections=connections,
        healthy_connections=healthy,
        updated_at=time.monotonic(),
        loaded=True,
    )


def _load_flapping_from_health_payload(
    slug: str,
    payload: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
) -> None:
    raw_flapping = payload.get("flapping")
    if not isinstance(raw_flapping, dict):
        return
    policy = flapping_policy_from_config(connection_health_flapping_settings(config))
    load_flapping_state_from_disk(slug, raw_flapping, policy=policy)


def apply_health_snapshot(
    slug: str,
    snapshot: ConnectionHealthSnapshot,
    *,
    config: dict[str, Any] | None = None,
    update_flapping: bool = True,
) -> EligibilityDelta | None:
    cfg = config or load_config()
    policy = flapping_policy_from_config(connection_health_flapping_settings(cfg))
    previous = snapshot_health_for_catalog(slug)
    previous_healthy = previous.healthy_connections if previous else set()
    previous_gated = gated_connections(slug, policy=policy) if previous else set()

    if update_flapping:
        update_flapping_states(
            slug,
            list(snapshot.connections.values()),
            policy=policy,
        )
    current_gated = gated_connections(slug, policy=policy)
    delta = compute_eligibility_delta(
        previous_healthy=previous_healthy,
        previous_gated=previous_gated,
        current_healthy=snapshot.healthy_connections,
        current_gated=current_gated,
    )
    with _health_lock:
        snapshot.loaded = True
        _health_states[slug] = snapshot
    return delta


def apply_connections_full_replace(
    slug: str,
    connections: list[dict[str, Any]],
    *,
    config: dict[str, Any] | None = None,
) -> tuple[ConnectionHealthSnapshot, EligibilityDelta | None]:
    """Replace the in-memory connection dict from a bulk GET response."""
    connections_dict = connections_list_to_dict(connections)
    snapshot = ConnectionHealthSnapshot(
        connections=copy.deepcopy(connections_dict),
        healthy_connections=build_healthy_connections(connections_dict),
        updated_at=time.monotonic(),
        loaded=True,
    )
    delta = apply_health_snapshot(slug, snapshot, config=config, update_flapping=True)
    return snapshot, delta


def load_health_snapshot_from_disk(
    slug: str,
    payload: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
) -> bool:
    snapshot = health_snapshot_from_disk(payload)
    if snapshot is None:
        return False
    _load_flapping_from_health_payload(slug, payload, config=config)
    apply_health_snapshot(slug, snapshot, config=config, update_flapping=False)
    return True


def snapshot_health_for_catalog(slug: str) -> ConnectionHealthSnapshot | None:
    with _health_lock:
        state = _health_states.get(slug)
        if state is None:
            return None
        return ConnectionHealthSnapshot(
            connections=copy.deepcopy(state.connections),
            healthy_connections=set(state.healthy_connections),
            updated_at=state.updated_at,
            loaded=state.loaded,
        )


def health_cache_loaded(slug: str) -> bool:
    with _health_lock:
        state = _health_states.get(slug)
        return state is not None and state.loaded


def connection_fingerprint_for_slug(slug: str) -> str:
    snapshot = snapshot_health_for_catalog(slug)
    if snapshot is None:
        return ""
    return connections_fingerprint(snapshot.connections)


def connection_health_snapshot_fields(
    slug: str,
    *,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fields for ``executor_catalog_health_snapshot``."""
    snapshot = snapshot_health_for_catalog(slug)
    if snapshot is None:
        return {
            "connection_health_loaded": False,
            "healthy_connection_count": 0,
            "total_connection_count": 0,
            "flapping_enabled": False,
            "gated_connection_count": 0,
        }

    cfg = config or load_config()
    policy = flapping_policy_from_config(connection_health_flapping_settings(cfg))
    age_seconds = time.monotonic() - snapshot.updated_at if snapshot.updated_at else None
    payload: dict[str, Any] = {
        "connection_health_loaded": snapshot.loaded,
        "healthy_connection_count": len(snapshot.healthy_connections),
        "total_connection_count": len(snapshot.connections),
    }
    payload.update(flapping_snapshot_fields(slug, policy=policy))
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


def _merge_probe_result(conn: dict[str, Any], result: dict[str, Any]) -> None:
    last_health: dict[str, Any] = {
        "status": result.get("status"),
        "checkedAt": result.get("checkedAt"),
    }
    detail = result.get("detail")
    if detail is not None:
        last_health["detail"] = detail
    conn["lastHealth"] = last_health


async def refresh_connection_health_async(
    *,
    base_url: str,
    token: str | None,
    slug: str,
    config: dict[str, Any] | None = None,
    concurrency: int | None = None,
) -> tuple[ConnectionHealthSnapshot, EligibilityDelta | None]:
    """Bootstrap GET, wave POST probes, end GET full replace."""
    cfg = config or load_config()
    cache_settings = tools_hook_executor_cache_settings(cfg)
    probe_concurrency = concurrency or int(cache_settings.get("health_probe_concurrency") or 4)
    probe_concurrency = max(1, probe_concurrency)

    bootstrap = await fetch_connections_async(base_url=base_url, token=token)
    working = copy.deepcopy(connections_list_to_dict(bootstrap))
    probe_queue = list(working.keys())

    if probe_queue:
        headers = _auth_headers(token)
        timeout = httpx.Timeout(60.0, connect=10.0)
        semaphore = asyncio.Semaphore(probe_concurrency)
        async with httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=timeout,
        ) as client:

            async def probe_one(key: ConnectionKey) -> None:
                async with semaphore:
                    result = await probe_connection_health_async(client, key)
                if result is None:
                    return
                conn = working.get(key)
                if conn is not None:
                    _merge_probe_result(conn, result)

            for offset in range(0, len(probe_queue), probe_concurrency):
                end = offset + probe_concurrency
                wave = probe_queue[offset:end]
                await asyncio.gather(*(probe_one(key) for key in wave))
                wave_snapshot = ConnectionHealthSnapshot(
                    connections=copy.deepcopy(working),
                    healthy_connections=build_healthy_connections(working),
                    updated_at=time.monotonic(),
                    loaded=True,
                )
                apply_health_snapshot(slug, wave_snapshot, config=cfg, update_flapping=True)

    final_list = await fetch_connections_async(base_url=base_url, token=token)
    snapshot, delta = apply_connections_full_replace(slug, final_list, config=cfg)
    logger.info(
        "executor connection health refreshed connections=%d healthy_connections=%d",
        len(snapshot.connections),
        len(snapshot.healthy_connections),
    )
    return snapshot, delta


def merge_tool_metadata(tool: dict[str, Any], metadata: dict[str, Any] | None) -> None:
    """Attach routing metadata from the tools list onto a normalized tool dict."""
    if not metadata:
        return
    for key in _TOOL_METADATA_KEYS:
        if key in metadata and metadata[key] is not None:
            tool[key] = metadata[key]
