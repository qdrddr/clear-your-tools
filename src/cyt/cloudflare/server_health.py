"""Upstream server health cache for Cloudflare portal hook injection."""

from __future__ import annotations

import copy
import hashlib
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from cyt.cloudflare.runtime import connection_health_flapping_settings, load_config
from cyt.cloudflare.server_flapping import (
    ServerKey,
    clear_flapping_cache,
    flapping_policy_from_config,
    flapping_snapshot_fields,
    flapping_state_to_disk,
    gated_servers,
    load_flapping_state_from_disk,
    update_flapping_states,
)

logger = logging.getLogger(__name__)

_health_lock = threading.Lock()
_health_states: dict[str, ServerHealthSnapshot] = {}
_permissive_filter_logged: set[str] = set()
_debug_disk_enabled = False


@dataclass
class ServerHealthSnapshot:
    servers: dict[ServerKey, dict[str, Any]] = field(default_factory=dict)
    enabled_servers: set[ServerKey] = field(default_factory=set)
    updated_at: float = 0.0
    loaded: bool = False


def set_cloudflare_debug_disk(enabled: bool) -> None:
    global _debug_disk_enabled
    _debug_disk_enabled = enabled


def debug_disk_enabled() -> bool:
    return _debug_disk_enabled


def clear_server_health_cache() -> None:
    with _health_lock:
        _health_states.clear()
        _permissive_filter_logged.clear()
    clear_flapping_cache()


def server_key_from_tool(tool: dict[str, Any]) -> ServerKey | None:
    server_id = str(tool.get("cloudflare_server_id") or "").strip()
    if not server_id:
        return None
    return ServerKey(server_id=server_id)


def servers_list_to_dict(servers: list[dict[str, Any]]) -> dict[ServerKey, dict[str, Any]]:
    result: dict[ServerKey, dict[str, Any]] = {}
    for server in servers:
        server_id = str(
            server.get("id") or server.get("server_id") or server.get("name") or "",
        ).strip()
        if not server_id:
            continue
        key = ServerKey(server_id=server_id)
        result[key] = copy.deepcopy(server)
    return result


def enabled_server_keys(servers: list[dict[str, Any]]) -> set[ServerKey]:
    enabled: set[ServerKey] = set()
    for server in servers:
        server_id = str(
            server.get("id") or server.get("server_id") or server.get("name") or "",
        ).strip()
        if not server_id:
            continue
        flag = server.get("enabled")
        if flag is None:
            flag = server.get("is_enabled", True)
        if bool(flag):
            enabled.add(ServerKey(server_id=server_id))
    return enabled


def refresh_server_health(
    *,
    slug: str,
    servers: list[dict[str, Any]],
    config: dict[str, Any] | None = None,
) -> None:
    cfg = config or load_config()
    policy = flapping_policy_from_config(connection_health_flapping_settings(cfg))
    statuses = {
        key: ("enabled" if key in enabled_server_keys(servers) else "disabled")
        for key in servers_list_to_dict(servers)
    }
    update_flapping_states(slug, statuses, policy=policy)
    snapshot = ServerHealthSnapshot(
        servers=servers_list_to_dict(servers),
        enabled_servers=enabled_server_keys(servers),
        updated_at=time.monotonic(),
        loaded=True,
    )
    with _health_lock:
        _health_states[slug] = snapshot


def load_server_health_from_disk(
    slug: str,
    payload: dict[str, Any] | None,
    *,
    config: dict[str, Any] | None = None,
) -> None:
    if not isinstance(payload, dict):
        return
    servers_raw = payload.get("servers")
    if not isinstance(servers_raw, list):
        return
    refresh_server_health(slug=slug, servers=servers_raw, config=config)
    flapping = payload.get("flapping")
    if isinstance(flapping, dict):
        load_flapping_state_from_disk(slug, flapping)


def snapshot_health_for_catalog(slug: str) -> ServerHealthSnapshot | None:
    with _health_lock:
        snapshot = _health_states.get(slug)
        if snapshot is None:
            return None
        return copy.deepcopy(snapshot)


def health_snapshot_to_disk(
    snapshot: ServerHealthSnapshot,
    *,
    slug: str,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _ = config
    servers = [
        snapshot.servers[key] for key in sorted(snapshot.servers, key=lambda item: item.server_id)
    ]
    return {
        "servers": servers,
        "flapping": flapping_state_to_disk(slug),
    }


def server_health_snapshot_fields(slug: str) -> dict[str, Any]:
    with _health_lock:
        snapshot = _health_states.get(slug)
        if snapshot is None:
            return {"server_health_loaded": False}
        return {
            "server_health_loaded": snapshot.loaded,
            "enabled_server_count": len(snapshot.enabled_servers),
            "tracked_server_count": len(snapshot.servers),
            **flapping_snapshot_fields(slug),
        }


def server_fingerprint_for_slug(slug: str) -> str:
    with _health_lock:
        snapshot = _health_states.get(slug)
        if snapshot is None or not snapshot.loaded:
            return ""
        parts = sorted(
            f"{key.server_id}:{'1' if key in snapshot.enabled_servers else '0'}"
            for key in snapshot.servers
        )
    payload = "|".join(parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def eligible_server_ids(slug: str, *, config: dict[str, Any] | None = None) -> set[str] | None:
    _ = config
    with _health_lock:
        snapshot = _health_states.get(slug)
        if snapshot is None or not snapshot.loaded:
            return None
        if not snapshot.servers:
            return None
        gated = gated_servers(slug)
        return {key.server_id for key in snapshot.enabled_servers if key not in gated}


def filter_catalog_by_server_health(
    tools: list[dict[str, Any]],
    slug: str,
    *,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    eligible = eligible_server_ids(slug, config=config)
    if eligible is None:
        if slug not in _permissive_filter_logged:
            logger.info(
                "cloudflare server health permissive bootstrap slug=%s tool_count=%d",
                slug,
                len(tools),
            )
            _permissive_filter_logged.add(slug)
        return tools
    filtered: list[dict[str, Any]] = []
    for tool in tools:
        key = server_key_from_tool(tool)
        if key is None:
            filtered.append(tool)
            continue
        if key.server_id in eligible:
            filtered.append(tool)
    return filtered
