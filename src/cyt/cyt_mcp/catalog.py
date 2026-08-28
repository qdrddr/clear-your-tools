"""Load tool catalogs from cyt-mcp (CLI subprocess or HTTP)."""

from __future__ import annotations

import copy
import json
import logging
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from cyt.config import (
    load_config,
    tools_hook_cyt_mcp_agent,
    tools_hook_cyt_mcp_catalog_url,
    tools_hook_cyt_mcp_executable,
    uses_cyt_mcp_tool_catalog,
)
from cyt.cyt_mcp.catalog_disk import (
    normalize_cyt_mcp_agent_slug,
    raw_catalog_content_hash,
    read_disk_catalog,
    write_disk_catalog,
)
from cyt.cyt_mcp.cli import cyt_mcp_available, run_cyt_mcp_catalog_json

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _CytMcpCacheKey:
    agent: str
    slug: str


@dataclass
class _CytMcpCatalogState:
    tools: list[dict[str, Any]] = field(default_factory=list)
    updated_at: float = 0.0
    catalog_content_hash: str = ""


@dataclass(frozen=True)
class CytMcpLiveCatalog:
    tools: list[dict[str, Any]]
    degraded_servers: tuple[str, ...]


_catalog_lock = threading.Lock()
_catalog_states: dict[_CytMcpCacheKey, _CytMcpCatalogState] = {}


def clear_cyt_mcp_catalog_cache() -> None:
    with _catalog_lock:
        _catalog_states.clear()
    from cyt.cyt_mcp.cache_scheduler import clear_cyt_mcp_cache_schedulers

    clear_cyt_mcp_cache_schedulers()


def _runtime_active(config: dict[str, Any]) -> bool:
    return uses_cyt_mcp_tool_catalog(config)


def _cache_key_for_config(config: dict[str, Any]) -> _CytMcpCacheKey:
    agent = tools_hook_cyt_mcp_agent(config)
    slug = normalize_cyt_mcp_agent_slug(agent)
    return _CytMcpCacheKey(agent=agent, slug=slug)


def cyt_mcp_catalog_slug(config: dict[str, Any] | None = None) -> str:
    return _cache_key_for_config(config or load_config()).slug


def _get_state(cache_key: _CytMcpCacheKey) -> _CytMcpCatalogState:
    with _catalog_lock:
        state = _catalog_states.get(cache_key)
        if state is None:
            state = _CytMcpCatalogState()
            _catalog_states[cache_key] = state
        return state


def _snapshot_tools(state: _CytMcpCatalogState) -> list[dict[str, Any]]:
    with _catalog_lock:
        return copy.deepcopy(state.tools)


def _apply_catalog_to_state(
    state: _CytMcpCatalogState,
    tools: list[dict[str, Any]],
    *,
    content_hash: str,
    config: dict[str, Any] | None = None,
) -> None:
    with _catalog_lock:
        state.tools = tools
        state.updated_at = time.monotonic()
        state.catalog_content_hash = content_hash
    if config is not None:
        from cyt.tools.master_cache_scheduler import schedule_master_catalog_refresh

        schedule_master_catalog_refresh(config)


def _normalize_tool(tool: dict[str, Any]) -> dict[str, Any] | None:
    name = str(tool.get("name") or "").strip()
    if not name:
        return None
    schema = tool.get("input_schema")
    if not isinstance(schema, dict):
        schema = tool.get("inputSchema")
    if not isinstance(schema, dict):
        schema = {}
    normalized: dict[str, Any] = {
        "name": name,
        "input_schema": schema,
        "cyt_catalog_source": "cyt_mcp",
    }
    if tool.get("description") is not None:
        normalized["description"] = str(tool["description"])
    server_key = tool.get("server_key")
    tool_name = tool.get("tool_name")
    if isinstance(server_key, str) and server_key.strip():
        normalized["server_key"] = server_key.strip()
    if isinstance(tool_name, str) and tool_name.strip():
        normalized["tool_name"] = tool_name.strip()
    elif "_" in name:
        prefix, _, suffix = name.partition("_")
        if prefix and suffix:
            normalized["server_key"] = prefix
            normalized["tool_name"] = suffix
    return normalized


def _normalize_catalog_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    tools_raw = payload.get("tools")
    if not isinstance(tools_raw, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in tools_raw:
        if not isinstance(item, dict):
            continue
        tool = _normalize_tool(item)
        if tool is not None:
            normalized.append(tool)
    return normalized


def _degraded_servers_from_payload(payload: dict[str, Any]) -> tuple[str, ...]:
    raw = payload.get("degraded_servers")
    if not isinstance(raw, list):
        return ()
    degraded: list[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            degraded.append(item.strip())
    return tuple(degraded)


def _tool_server_key(tool: dict[str, Any]) -> str:
    server_key = tool.get("server_key")
    if isinstance(server_key, str) and server_key.strip():
        return server_key.strip()
    name = str(tool.get("name") or "")
    prefix, _, suffix = name.partition("_")
    if prefix and suffix:
        return prefix
    return ""


def _disk_catalog_tools(cache_key: _CytMcpCacheKey) -> list[dict[str, Any]]:
    envelope = read_disk_catalog(cache_key.slug)
    if envelope is None:
        return []
    tools = envelope.get("tools")
    if not isinstance(tools, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in tools:
        if not isinstance(item, dict):
            continue
        tool = _normalize_tool(item)
        if tool is not None:
            normalized.append(tool)
    return normalized


def _server_keys_for_tools(tools: Sequence[dict[str, Any]]) -> set[str]:
    return {key for key in (_tool_server_key(tool) for tool in tools) if key}


def _preserve_tools_for_servers(
    new_tools: list[dict[str, Any]],
    fallback_sources: Sequence[Sequence[dict[str, Any]]],
    server_keys: set[str],
) -> list[dict[str, Any]]:
    if not server_keys:
        return new_tools
    new_names = {str(tool.get("name", "")) for tool in new_tools if str(tool.get("name", ""))}
    preserved: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in fallback_sources:
        for tool in source:
            name = str(tool.get("name", ""))
            if not name or name in new_names or name in seen:
                continue
            if _tool_server_key(tool) not in server_keys:
                continue
            preserved.append(copy.deepcopy(tool))
            seen.add(name)
    if not preserved:
        return new_tools
    return [*new_tools, *preserved]


def _merge_degraded_backend_tools(
    new_tools: list[dict[str, Any]],
    existing_tools: list[dict[str, Any]],
    degraded_servers: Sequence[str],
    *,
    disk_tools: Sequence[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Keep prior tools for backends that failed to mount on this live fetch."""
    degraded = {
        server.strip() for server in degraded_servers if isinstance(server, str) and server.strip()
    }
    fallback_sources: list[list[dict[str, Any]]] = []
    if existing_tools:
        fallback_sources.append(list(existing_tools))
    if disk_tools:
        fallback_sources.append(list(disk_tools))

    servers_to_preserve = set(degraded)
    if disk_tools:
        disk_servers = _server_keys_for_tools(disk_tools)
        live_servers = _server_keys_for_tools(new_tools)
        servers_to_preserve |= disk_servers - live_servers

    if not servers_to_preserve or not fallback_sources:
        return new_tools

    merged = _preserve_tools_for_servers(new_tools, fallback_sources, servers_to_preserve)
    preserved_count = len(merged) - len(new_tools)
    if preserved_count:
        logger.info(
            "cyt-mcp catalog preserved %d tools for backends: %s",
            preserved_count,
            ", ".join(sorted(servers_to_preserve)),
        )
    return merged


def _hydrate_missing_servers_from_disk(
    cache_key: _CytMcpCacheKey,
    tools: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    disk_tools = _disk_catalog_tools(cache_key)
    if not disk_tools:
        return tools
    missing_servers = _server_keys_for_tools(disk_tools) - _server_keys_for_tools(tools)
    if not missing_servers:
        return tools
    merged = _preserve_tools_for_servers(tools, [disk_tools], missing_servers)
    if len(merged) > len(tools):
        logger.info(
            "cyt-mcp catalog hydrated %d tools from disk for backends: %s",
            len(merged) - len(tools),
            ", ".join(sorted(missing_servers)),
        )
    return merged


def _fetch_catalog_from_http(url: str) -> dict[str, Any] | None:
    catalog_url = str(url or "").strip().rstrip("/")
    if not catalog_url:
        return None
    try:
        with urlopen(catalog_url, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except (OSError, URLError, TimeoutError, ValueError) as exc:
        logger.warning("cyt-mcp catalog HTTP fetch failed: %s", exc)
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("cyt-mcp catalog HTTP returned invalid JSON")
        return None
    return payload if isinstance(payload, dict) else None


def _fetch_catalog_live(config: dict[str, Any], cache_key: _CytMcpCacheKey) -> CytMcpLiveCatalog:
    catalog_url = tools_hook_cyt_mcp_catalog_url(config)
    payload: dict[str, Any] | None = None
    if catalog_url:
        payload = _fetch_catalog_from_http(catalog_url)
    if payload is None:
        executable = tools_hook_cyt_mcp_executable(config)
        if not cyt_mcp_available(executable):
            logger.warning("cyt-mcp executable unavailable: %s", executable)
            return CytMcpLiveCatalog(tools=[], degraded_servers=())
        payload = run_cyt_mcp_catalog_json(executable, agent=cache_key.agent)
    if payload is None:
        return CytMcpLiveCatalog(tools=[], degraded_servers=())
    return CytMcpLiveCatalog(
        tools=_normalize_catalog_payload(payload),
        degraded_servers=_degraded_servers_from_payload(payload),
    )


def _write_catalog_disk(cache_key: _CytMcpCacheKey, tools: list[dict[str, Any]]) -> None:
    content_hash = raw_catalog_content_hash(tools)
    write_disk_catalog(
        cache_key.slug,
        agent=cache_key.agent,
        tools=tools,
        content_hash=content_hash,
    )


def _load_catalog_from_disk(
    cache_key: _CytMcpCacheKey,
    *,
    config: dict[str, Any] | None = None,
) -> bool:
    envelope = read_disk_catalog(cache_key.slug)
    if envelope is None:
        return False
    tools = envelope.get("tools")
    if not isinstance(tools, list):
        return False
    content_hash = str(envelope.get("catalog_content_hash") or raw_catalog_content_hash(tools))
    state = _get_state(cache_key)
    _apply_catalog_to_state(
        state,
        copy.deepcopy(tools),
        content_hash=content_hash,
        config=config,
    )
    logger.info(
        "cyt-mcp catalog disk_hit slug=%s catalog_content_hash=%s tool_count=%d",
        cache_key.slug,
        content_hash[:12],
        len(tools),
    )
    return True


def load_cyt_mcp_catalog_from_disk(config: dict[str, Any] | None = None) -> bool:
    cfg = config or load_config()
    if not _runtime_active(cfg):
        return False
    cache_key = _cache_key_for_config(cfg)
    state = _get_state(cache_key)
    with _catalog_lock:
        if state.tools:
            return True
    return _load_catalog_from_disk(cache_key, config=cfg)


def _ensure_scheduler_started(cfg: dict[str, Any]) -> None:
    from cyt.cyt_mcp.cache_scheduler import start_cyt_mcp_cache_scheduler

    start_cyt_mcp_cache_scheduler(cfg)


def _blocking_fetch(cfg: dict[str, Any], cache_key: _CytMcpCacheKey) -> list[dict[str, Any]]:
    state = _get_state(cache_key)
    try:
        logger.info("cyt-mcp catalog fetch slug=%s blocking=true", cache_key.slug)
        fetched = _fetch_catalog_live(cfg, cache_key)
    except Exception as exc:
        logger.warning("cyt-mcp catalog fetch failed: %s", exc)
        return _snapshot_tools(state)
    if not fetched.tools:
        return _snapshot_tools(state)
    apply_fetched_catalog(
        cfg,
        fetched.tools,
        degraded_servers=fetched.degraded_servers,
    )
    _ensure_scheduler_started(cfg)
    return _snapshot_tools(state)


def _get_cyt_mcp_catalog_impl(
    cfg: dict[str, Any],
    *,
    blocking: bool,
    force: bool,
) -> list[dict[str, Any]] | None:
    cache_key = _cache_key_for_config(cfg)
    state = _get_state(cache_key)

    with _catalog_lock:
        has_memory = bool(state.tools)

    if not has_memory:
        _load_catalog_from_disk(cache_key, config=cfg)
        with _catalog_lock:
            has_memory = bool(state.tools)

    if not has_memory and (blocking or force):
        return _blocking_fetch(cfg, cache_key)

    if force and has_memory:
        return _blocking_fetch(cfg, cache_key)

    if not has_memory:
        _ensure_scheduler_started(cfg)
        from cyt.cyt_mcp.cache_scheduler import schedule_cyt_mcp_catalog_refresh

        schedule_cyt_mcp_catalog_refresh(cfg, force=True)
        return None

    if blocking:
        return _blocking_fetch(cfg, cache_key)

    _ensure_scheduler_started(cfg)
    from cyt.cyt_mcp.cache_scheduler import schedule_cyt_mcp_catalog_refresh

    schedule_cyt_mcp_catalog_refresh(cfg)
    snapshot = _snapshot_tools(state)
    hydrated = _hydrate_missing_servers_from_disk(cache_key, snapshot)
    if len(hydrated) != len(snapshot):
        content_hash = raw_catalog_content_hash(hydrated)
        _apply_catalog_to_state(state, hydrated, content_hash=content_hash, config=cfg)
        return copy.deepcopy(hydrated)
    return snapshot


def get_cyt_mcp_catalog(
    config: dict[str, Any] | None = None,
    *,
    blocking: bool = False,
    force: bool = False,
) -> list[dict[str, Any]] | None:
    cfg = config or load_config()
    if not _runtime_active(cfg):
        return None
    return _get_cyt_mcp_catalog_impl(cfg, blocking=blocking, force=force)


def cyt_mcp_catalog_fingerprint(config: dict[str, Any] | None = None) -> str:
    cfg = config or load_config()
    if not _runtime_active(cfg):
        return ""
    cache_key = _cache_key_for_config(cfg)
    state = _get_state(cache_key)
    with _catalog_lock:
        if state.catalog_content_hash:
            return state.catalog_content_hash
    envelope = read_disk_catalog(cache_key.slug)
    if envelope is None:
        return ""
    return str(envelope.get("catalog_content_hash") or "")


def apply_fetched_catalog(
    config: dict[str, Any],
    tools: list[dict[str, Any]],
    *,
    degraded_servers: Sequence[str] | None = None,
) -> None:
    cache_key = _cache_key_for_config(config)
    state = _get_state(cache_key)
    existing = _snapshot_tools(state)
    disk_tools = _disk_catalog_tools(cache_key)
    merged = _merge_degraded_backend_tools(
        tools,
        existing,
        degraded_servers or (),
        disk_tools=disk_tools,
    )
    # #region agent log
    try:
        import json as _json
        import time as _time
        from pathlib import Path as _Path

        _log_path = _Path("debug-303753.log")
        _log_path.parent.mkdir(parents=True, exist_ok=True)
        with _log_path.open("a", encoding="utf-8") as _fh:
            _fh.write(
                _json.dumps(
                    {
                        "sessionId": "303753",
                        "runId": "post-fix",
                        "hypothesisId": "B",
                        "location": "cyt/cyt_mcp/catalog.py:apply_fetched_catalog",
                        "message": "cyt-mcp catalog apply",
                        "data": {
                            "incoming_count": len(tools),
                            "existing_count": len(existing),
                            "disk_count": len(disk_tools),
                            "merged_count": len(merged),
                            "degraded_servers": list(degraded_servers or ()),
                            "preserved_count": len(merged) - len(tools),
                            "live_servers": sorted(_server_keys_for_tools(tools)),
                            "merged_servers": sorted(_server_keys_for_tools(merged)),
                        },
                        "timestamp": int(_time.time() * 1000),
                    },
                    ensure_ascii=False,
                )
                + "\n",
            )
    except OSError:
        pass
    # #endregion
    content_hash = raw_catalog_content_hash(merged)
    _apply_catalog_to_state(state, merged, content_hash=content_hash, config=config)
    _write_catalog_disk(cache_key, merged)
