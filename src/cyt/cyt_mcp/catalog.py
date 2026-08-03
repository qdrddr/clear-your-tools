"""Load tool catalogs from cyt-mcp (CLI subprocess or HTTP)."""

from __future__ import annotations

import copy
import json
import logging
import threading
import time
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


def _fetch_catalog_live(config: dict[str, Any], cache_key: _CytMcpCacheKey) -> list[dict[str, Any]]:
    catalog_url = tools_hook_cyt_mcp_catalog_url(config)
    payload: dict[str, Any] | None = None
    if catalog_url:
        payload = _fetch_catalog_from_http(catalog_url)
    if payload is None:
        executable = tools_hook_cyt_mcp_executable(config)
        if not cyt_mcp_available(executable):
            logger.warning("cyt-mcp executable unavailable: %s", executable)
            return []
        payload = run_cyt_mcp_catalog_json(executable, agent=cache_key.agent)
    if payload is None:
        return []
    return _normalize_catalog_payload(payload)


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
        tools = _fetch_catalog_live(cfg, cache_key)
    except Exception as exc:
        logger.warning("cyt-mcp catalog fetch failed: %s", exc)
        return _snapshot_tools(state)
    if not tools:
        return _snapshot_tools(state)
    content_hash = raw_catalog_content_hash(tools)
    _apply_catalog_to_state(state, tools, content_hash=content_hash, config=cfg)
    _write_catalog_disk(cache_key, tools)
    _ensure_scheduler_started(cfg)
    return copy.deepcopy(tools)


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
    return _snapshot_tools(state)


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
) -> None:
    cache_key = _cache_key_for_config(config)
    content_hash = raw_catalog_content_hash(tools)
    state = _get_state(cache_key)
    _apply_catalog_to_state(state, tools, content_hash=content_hash, config=config)
    _write_catalog_disk(cache_key, tools)
