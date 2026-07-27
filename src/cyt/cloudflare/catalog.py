"""Load tool catalogs from the Cloudflare MCP portal."""

from __future__ import annotations

import asyncio
import copy
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from cyt.cloudflare.catalog_disk import (
    normalize_cloudflare_url_slug,
    raw_catalog_content_hash,
    read_disk_catalog,
    write_disk_catalog,
)
from cyt.cloudflare.mcp import (
    cloudflare_portal_base_url,
    fetch_cloudflare_tools_list_async,
    fetch_portal_list_servers_async,
    filter_excluded_cloudflare_tools,
)
from cyt.cloudflare.runtime import (
    load_config,
    resolve_credential,
    tools_hook_cloudflare_access_client_id_var,
    tools_hook_cloudflare_access_client_secret_var,
    tools_hook_cloudflare_url,
    uses_cloudflare_tool_catalog,
)
from cyt.cloudflare.server_health import (
    clear_server_health_cache,
    debug_disk_enabled,
    filter_catalog_by_server_health,
    health_snapshot_to_disk,
    load_server_health_from_disk,
    refresh_server_health,
    server_health_snapshot_fields,
    snapshot_health_for_catalog,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _CloudflareCacheKey:
    portal_url: str
    slug: str


@dataclass
class _CloudflareCatalogState:
    tools: list[dict[str, Any]] = field(default_factory=list)
    updated_at: float = 0.0
    catalog_content_hash: str = ""


_catalog_lock = threading.Lock()
_catalog_states: dict[_CloudflareCacheKey, _CloudflareCatalogState] = {}


def clear_cloudflare_catalog_cache() -> None:
    with _catalog_lock:
        _catalog_states.clear()
    clear_server_health_cache()
    from cyt.cloudflare.cache_scheduler import clear_cloudflare_cache_schedulers

    clear_cloudflare_cache_schedulers()


def _cloudflare_runtime_active(config: dict[str, Any]) -> bool:
    return uses_cloudflare_tool_catalog(config)


def _cache_key_for_config(config: dict[str, Any]) -> _CloudflareCacheKey:
    portal_url = tools_hook_cloudflare_url(config)
    normalized_url = cloudflare_portal_base_url(portal_url) or portal_url
    slug = normalize_cloudflare_url_slug(portal_url)
    return _CloudflareCacheKey(portal_url=normalized_url, slug=slug)


def cloudflare_catalog_available_locally(config: dict[str, Any] | None = None) -> bool:
    """True when in-memory or on-disk catalog has at least one tool."""
    cfg = config or load_config()
    if not _cloudflare_runtime_active(cfg):
        return False
    cache_key = _cache_key_for_config(cfg)
    state = _get_state(cache_key)
    with _catalog_lock:
        if state.tools:
            return True
    envelope = read_disk_catalog(cache_key.slug)
    if envelope is None:
        return False
    tools = envelope.get("tools")
    return isinstance(tools, list) and bool(tools)


def _get_state(cache_key: _CloudflareCacheKey) -> _CloudflareCatalogState:
    with _catalog_lock:
        state = _catalog_states.get(cache_key)
        if state is None:
            state = _CloudflareCatalogState()
            _catalog_states[cache_key] = state
        return state


def _snapshot_tools(state: _CloudflareCatalogState) -> list[dict[str, Any]]:
    with _catalog_lock:
        return copy.deepcopy(state.tools)


def _apply_catalog_to_state(
    state: _CloudflareCatalogState,
    tools: list[dict[str, Any]],
    *,
    content_hash: str,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    filtered = filter_excluded_cloudflare_tools(tools)
    if len(filtered) != len(tools):
        content_hash = raw_catalog_content_hash(filtered)
    with _catalog_lock:
        state.tools = filtered
        state.updated_at = time.monotonic()
        state.catalog_content_hash = content_hash
    if config is not None:
        from cyt.tools.master_cache_scheduler import schedule_master_catalog_refresh

        schedule_master_catalog_refresh(config)
    return filtered


def _resolve_access_credentials(
    config: dict[str, Any],
    *,
    allow_prompt: bool,
) -> tuple[str | None, str | None]:
    client_id_var = tools_hook_cloudflare_access_client_id_var(config)
    secret_var = tools_hook_cloudflare_access_client_secret_var(config)
    client_id, _ = resolve_credential(client_id_var, allow_prompt=allow_prompt)
    client_secret, _ = resolve_credential(secret_var, allow_prompt=allow_prompt)
    return client_id, client_secret


def _fetch_catalog_from_network(
    config: dict[str, Any],
    *,
    allow_prompt: bool,
) -> list[dict[str, Any]]:
    portal_url = tools_hook_cloudflare_url(config)
    client_id, client_secret = _resolve_access_credentials(config, allow_prompt=allow_prompt)
    if not portal_url or not client_id or not client_secret:
        raise ValueError("cloudflare portal URL or CF Access credentials missing")
    cache_key = _cache_key_for_config(config)
    tools = asyncio.run(
        fetch_cloudflare_tools_list_async(
            portal_url=portal_url,
            client_id=client_id,
            client_secret=client_secret,
        ),
    )
    try:
        servers = asyncio.run(
            fetch_portal_list_servers_async(
                portal_url=portal_url,
                client_id=client_id,
                client_secret=client_secret,
            ),
        )
        refresh_server_health(slug=cache_key.slug, servers=servers, config=config)
    except Exception as exc:
        logger.warning("cloudflare portal_list_servers failed: %s", exc)
    return tools


def _write_catalog_disk(
    cache_key: _CloudflareCacheKey,
    *,
    tools: list[dict[str, Any]],
    config: dict[str, Any] | None = None,
) -> None:
    cfg = config or load_config()
    servers_health = None
    if debug_disk_enabled():
        snapshot = snapshot_health_for_catalog(cache_key.slug)
        if snapshot is not None:
            servers_health = health_snapshot_to_disk(snapshot, slug=cache_key.slug, config=cfg)
    write_disk_catalog(
        cache_key.slug,
        portal_url=cache_key.portal_url,
        tools=tools,
        content_hash=raw_catalog_content_hash(tools),
        servers_health=servers_health,
    )


def _load_catalog_from_disk(
    cache_key: _CloudflareCacheKey,
    *,
    config: dict[str, Any] | None = None,
) -> bool:
    envelope = read_disk_catalog(cache_key.slug)
    if envelope is None:
        return False
    tools = envelope.get("tools")
    if not isinstance(tools, list):
        return False
    state = _get_state(cache_key)
    filtered = _apply_catalog_to_state(
        state,
        copy.deepcopy(tools),
        content_hash=str(envelope.get("catalog_content_hash") or raw_catalog_content_hash(tools)),
        config=config,
    )
    load_server_health_from_disk(
        cache_key.slug,
        envelope.get("servers_health")
        if isinstance(envelope.get("servers_health"), dict)
        else None,
        config=config,
    )
    logger.info(
        "cloudflare catalog disk_hit slug=%s catalog_content_hash=%s tool_count=%d",
        cache_key.slug,
        state.catalog_content_hash[:12],
        len(filtered),
    )
    return True


def load_cloudflare_catalog_from_disk(config: dict[str, Any] | None = None) -> bool:
    cfg = config or load_config()
    if not _cloudflare_runtime_active(cfg):
        return False
    cache_key = _cache_key_for_config(cfg)
    state = _get_state(cache_key)
    with _catalog_lock:
        if state.tools:
            return True
    return _load_catalog_from_disk(cache_key, config=cfg)


def _ensure_scheduler_started(cfg: dict[str, Any]) -> None:
    from cyt.cloudflare.cache_scheduler import start_cloudflare_cache_scheduler

    start_cloudflare_cache_scheduler(cfg)


def _return_catalog(
    tools: list[dict[str, Any]],
    cache_key: _CloudflareCacheKey,
    *,
    apply_health_filter: bool,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not tools:
        return []
    if not apply_health_filter:
        return tools
    return filter_catalog_by_server_health(tools, cache_key.slug, config=config)


def _blocking_network_fetch(
    cfg: dict[str, Any],
    cache_key: _CloudflareCacheKey,
    *,
    allow_prompt: bool,
    apply_health_filter: bool = True,
) -> list[dict[str, Any]]:
    state = _get_state(cache_key)
    try:
        logger.info("cloudflare catalog network_fetch slug=%s blocking=true", cache_key.slug)
        tools = _fetch_catalog_from_network(cfg, allow_prompt=allow_prompt)
    except Exception as exc:
        logger.warning("cloudflare catalog fetch failed: %s", exc)
        stale = _snapshot_tools(state)
        return _return_catalog(
            stale,
            cache_key,
            apply_health_filter=apply_health_filter,
            config=cfg,
        )
    content_hash = raw_catalog_content_hash(tools)
    filtered = _apply_catalog_to_state(state, tools, content_hash=content_hash, config=cfg)
    _write_catalog_disk(cache_key, tools=filtered, config=cfg)
    _ensure_scheduler_started(cfg)
    return _return_catalog(
        copy.deepcopy(tools),
        cache_key,
        apply_health_filter=apply_health_filter,
        config=cfg,
    )


def _get_cloudflare_catalog_impl(
    cfg: dict[str, Any],
    *,
    blocking: bool,
    force: bool,
    allow_prompt: bool,
    apply_health_filter: bool = True,
) -> list[dict[str, Any]] | None:
    cache_key = _cache_key_for_config(cfg)
    state = _get_state(cache_key)

    with _catalog_lock:
        has_memory = bool(state.tools)

    if not has_memory:
        _load_catalog_from_disk(cache_key, config=cfg)
        with _catalog_lock:
            has_memory = bool(state.tools)

    if not has_memory:
        if blocking or force:
            return _blocking_network_fetch(
                cfg,
                cache_key,
                allow_prompt=allow_prompt,
                apply_health_filter=apply_health_filter,
            )
        _ensure_scheduler_started(cfg)
        return _return_catalog([], cache_key, apply_health_filter=apply_health_filter, config=cfg)

    if force and blocking:
        return _blocking_network_fetch(
            cfg,
            cache_key,
            allow_prompt=allow_prompt,
            apply_health_filter=apply_health_filter,
        )

    logger.debug(
        "cloudflare catalog cache_hit slug=%s tool_count=%d",
        cache_key.slug,
        len(state.tools),
    )
    _ensure_scheduler_started(cfg)
    return _return_catalog(
        _snapshot_tools(state),
        cache_key,
        apply_health_filter=apply_health_filter,
        config=cfg,
    )


def get_cloudflare_catalog(
    config: dict[str, Any] | None = None,
    *,
    blocking: bool = False,
    force: bool = False,
    allow_prompt: bool = False,
    apply_health_filter: bool = True,
) -> list[dict[str, Any]] | None:
    cfg = config or load_config()
    if not _cloudflare_runtime_active(cfg):
        return None
    return _get_cloudflare_catalog_impl(
        cfg,
        blocking=blocking,
        force=force,
        allow_prompt=allow_prompt,
        apply_health_filter=apply_health_filter,
    )


def load_cloudflare_tools(
    config: dict[str, Any] | None = None,
    *,
    blocking: bool = False,
) -> list[dict[str, Any]] | None:
    return get_cloudflare_catalog(config, blocking=blocking, force=False)


def fetch_cloudflare_tools_for_cli(
    config: dict[str, Any],
    *,
    allow_prompt: bool = True,
    blocking: bool = True,
    apply_health_filter: bool = True,
) -> list[dict[str, Any]]:
    tools = get_cloudflare_catalog(
        config,
        blocking=blocking,
        force=True,
        allow_prompt=allow_prompt,
        apply_health_filter=apply_health_filter,
    )
    return tools or []


def cloudflare_catalog_slug(config: dict[str, Any] | None = None) -> str:
    cfg = config or load_config()
    return _cache_key_for_config(cfg).slug


def cloudflare_catalog_fingerprint(config: dict[str, Any] | None = None) -> str:
    cfg = config or load_config()
    cache_key = _cache_key_for_config(cfg)
    state = _get_state(cache_key)
    with _catalog_lock:
        if state.catalog_content_hash:
            return state.catalog_content_hash
    envelope = read_disk_catalog(cache_key.slug)
    if envelope is None:
        return ""
    return str(envelope.get("catalog_content_hash") or "")


def cloudflare_catalog_health_snapshot(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config or load_config()
    if not _cloudflare_runtime_active(cfg):
        return {}
    cache_key = _cache_key_for_config(cfg)
    state = _get_state(cache_key)
    with _catalog_lock:
        tool_count = len(state.tools)
        age_seconds = time.monotonic() - state.updated_at if state.updated_at else None
        content_hash = state.catalog_content_hash
    payload: dict[str, Any] = {
        "portal_url_configured": bool(cache_key.portal_url),
        "catalog_tool_count": tool_count,
        "slug": cache_key.slug,
    }
    if age_seconds is not None:
        payload["catalog_age_seconds"] = round(age_seconds, 1)
    if content_hash:
        payload["catalog_content_hash_prefix"] = content_hash[:24]
    payload.update(server_health_snapshot_fields(cache_key.slug))
    return payload


def apply_fetched_catalog(
    cache_key: _CloudflareCacheKey,
    tools: list[dict[str, Any]],
    config: dict[str, Any] | None = None,
) -> None:
    state = _get_state(cache_key)
    content_hash = raw_catalog_content_hash(tools)
    _apply_catalog_to_state(state, tools, content_hash=content_hash, config=config)
