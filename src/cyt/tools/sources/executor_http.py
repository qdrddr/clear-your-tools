"""Load tool catalogs from the Executor MCP aggregator HTTP API."""

from __future__ import annotations

import asyncio
import copy
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from cyt.config import (
    load_config,
    tools_hook_executor_token_var,
    tools_hook_executor_url,
)
from cyt.launch.secrets import resolve_credential
from cyt.tools.sources.executor_catalog_disk import (
    normalize_executor_url_slug,
    raw_catalog_content_hash,
    read_disk_catalog,
    write_disk_catalog,
)
from cyt.tools.sources.executor_mcp import fetch_executor_mcp_cache_async

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 60.0
_DEFAULT_SCHEMA_CONCURRENCY = 16
_REFRESH_WAIT_SECONDS = 120.0
_LIST_PATH = "/api/tools"
_SCHEMA_PATH = "/api/tools/schema"


@dataclass(frozen=True)
class _ExecutorCacheKey:
    base_url: str
    token_fingerprint: str
    slug: str


@dataclass
class _ExecutorCatalogState:
    tools: list[dict[str, Any]] = field(default_factory=list)
    executor_mcp: dict[str, Any] | None = None
    updated_at: float = 0.0
    catalog_content_hash: str = ""
    refresh_in_progress: bool = False
    refresh_done: threading.Event = field(default_factory=threading.Event)


_catalog_lock = threading.Lock()
_catalog_states: dict[_ExecutorCacheKey, _ExecutorCatalogState] = {}


def clear_executor_catalog_cache() -> None:
    """Reset in-process executor catalog state (for tests)."""
    with _catalog_lock:
        _catalog_states.clear()


def _token_fingerprint(token: str | None) -> str:
    if not token:
        return ""
    return str(hash(token))


def _cache_key_for_config(config: dict[str, Any], token: str | None) -> _ExecutorCacheKey:
    base_url = tools_hook_executor_url(config)
    token_var = tools_hook_executor_token_var(config)
    slug = normalize_executor_url_slug(
        base_url,
        token_var=token_var if token else None,
    )
    return _ExecutorCacheKey(
        base_url,
        _token_fingerprint(token),
        slug,
    )


def _auth_headers(token: str | None) -> dict[str, str]:
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def _resolve_executor_token(
    config: dict[str, Any],
    *,
    allow_prompt: bool = True,
) -> str | None:
    token_var = tools_hook_executor_token_var(config)
    value, _source = resolve_credential(token_var, allow_prompt=allow_prompt)
    return value


def _normalize_tool(
    address: str,
    description: str | None,
    input_schema: dict[str, Any] | object,
) -> dict[str, Any]:
    tool: dict[str, Any] = {"name": address}
    if description:
        tool["description"] = str(description)
    if isinstance(input_schema, dict):
        tool["input_schema"] = input_schema
    return tool


async def _fetch_list_async(
    *,
    base_url: str,
    token: str | None,
) -> list[tuple[str, str | None]]:
    headers = _auth_headers(token)
    timeout = httpx.Timeout(60.0, connect=10.0)
    async with httpx.AsyncClient(headers=headers, timeout=timeout) as client:
        response = await client.get(f"{base_url}{_LIST_PATH}")
        response.raise_for_status()
        listed = response.json()
    if not isinstance(listed, list):
        raise ValueError("executor /api/tools response must be a JSON array")

    summaries: list[tuple[str, str | None]] = []
    for item in listed:
        if not isinstance(item, dict):
            continue
        address = str(item.get("address") or "").strip()
        if not address:
            continue
        description = item.get("description")
        desc_text = str(description) if description is not None else None
        summaries.append((address, desc_text))
    return summaries


async def _fetch_schema_async(
    client: httpx.AsyncClient,
    *,
    address: str,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any] | None:
    async with semaphore:
        try:
            response = await client.get(
                _SCHEMA_PATH,
                params={"address": address},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("executor schema fetch failed for %s: %s", address, exc)
            return None

    payload = response.json()
    if not isinstance(payload, dict):
        return None
    input_schema = payload.get("inputSchema") or payload.get("input_schema")
    description = payload.get("description")
    if not isinstance(input_schema, dict):
        input_schema = {}
    desc_text = str(description) if description is not None else None
    return _normalize_tool(address, desc_text, input_schema)


async def _fetch_schemas_async(
    *,
    base_url: str,
    token: str | None,
    addresses: list[str],
    descriptions: dict[str, str | None] | None = None,
    concurrency: int = _DEFAULT_SCHEMA_CONCURRENCY,
) -> list[dict[str, Any]]:
    if not addresses:
        return []

    headers = _auth_headers(token)
    timeout = httpx.Timeout(60.0, connect=10.0)
    descriptions = descriptions or {}
    semaphore = asyncio.Semaphore(max(1, concurrency))
    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=timeout) as client:
        tasks = [
            _fetch_schema_async(client, address=address, semaphore=semaphore)
            for address in addresses
        ]
        schemas = await asyncio.gather(*tasks)

    tools: list[dict[str, Any]] = []
    for address, schema_tool in zip(addresses, schemas, strict=True):
        if schema_tool is not None:
            tools.append(schema_tool)
            continue
        tools.append(_normalize_tool(address, descriptions.get(address), {}))
    return tools


async def _fetch_full_catalog_async(
    *,
    base_url: str,
    token: str | None,
    concurrency: int = _DEFAULT_SCHEMA_CONCURRENCY,
) -> list[dict[str, Any]]:
    summaries = await _fetch_list_async(base_url=base_url, token=token)
    addresses = [address for address, _ in summaries]
    descriptions = dict(summaries)
    return await _fetch_schemas_async(
        base_url=base_url,
        token=token,
        addresses=addresses,
        descriptions=descriptions,
        concurrency=concurrency,
    )


async def _fetch_catalog_and_mcp_async(
    *,
    base_url: str,
    token: str | None,
    concurrency: int = _DEFAULT_SCHEMA_CONCURRENCY,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Fetch REST tool schemas and MCP transport cache in parallel.

    MCP failures are non-fatal: returns ``(tools, None)`` so callers preserve any
    existing on-disk ``executor`` block.
    """
    catalog_task = asyncio.create_task(
        _fetch_full_catalog_async(
            base_url=base_url,
            token=token,
            concurrency=concurrency,
        ),
    )
    mcp_task = asyncio.create_task(
        fetch_executor_mcp_cache_async(base_url=base_url, token=token),
    )
    tools, mcp_outcome = await asyncio.gather(catalog_task, mcp_task, return_exceptions=True)

    if isinstance(tools, BaseException):
        raise tools

    executor_mcp: dict[str, Any] | None
    if isinstance(mcp_outcome, BaseException):
        logger.warning("executor MCP cache fetch failed: %s", mcp_outcome)
        executor_mcp = None
    else:
        executor_mcp = mcp_outcome
    return tools, executor_mcp


def _get_state(key: _ExecutorCacheKey) -> _ExecutorCatalogState:
    with _catalog_lock:
        state = _catalog_states.get(key)
        if state is None:
            state = _ExecutorCatalogState()
            _catalog_states[key] = state
        return state


def _snapshot_tools(state: _ExecutorCatalogState) -> list[dict[str, Any]]:
    with _catalog_lock:
        return copy.deepcopy(state.tools)


def _snapshot_executor_mcp(state: _ExecutorCatalogState) -> dict[str, Any] | None:
    with _catalog_lock:
        if state.executor_mcp is None:
            return None
        return copy.deepcopy(state.executor_mcp)


def _catalog_is_stale(state: _ExecutorCatalogState, *, now: float) -> bool:
    if not state.tools:
        return True
    if state.executor_mcp is None:
        return True
    return now - state.updated_at >= _CACHE_TTL_SECONDS


def _apply_catalog_to_state(
    state: _ExecutorCatalogState,
    tools: list[dict[str, Any]],
    *,
    content_hash: str | None = None,
    executor_mcp: dict[str, Any] | None = None,
) -> None:
    """Swap in-memory tools; update MCP only when a fresh ``executor_mcp`` is provided."""
    with _catalog_lock:
        state.tools = tools
        state.updated_at = time.monotonic()
        state.catalog_content_hash = content_hash or raw_catalog_content_hash(tools)
        if executor_mcp is not None:
            state.executor_mcp = executor_mcp


def _load_catalog_from_disk(cache_key: _ExecutorCacheKey) -> bool:
    envelope = read_disk_catalog(cache_key.slug)
    if envelope is None:
        return False
    tools = envelope.get("tools")
    if not isinstance(tools, list):
        return False
    content_hash = str(envelope.get("catalog_content_hash") or raw_catalog_content_hash(tools))
    raw_mcp = envelope.get("executor")
    executor_mcp = copy.deepcopy(raw_mcp) if isinstance(raw_mcp, dict) else None
    state = _get_state(cache_key)
    _apply_catalog_to_state(
        state,
        copy.deepcopy(tools),
        content_hash=content_hash,
        executor_mcp=executor_mcp,
    )
    logger.info(
        "executor catalog disk_hit slug=%s catalog_content_hash=%s tool_count=%d mcp=%s",
        cache_key.slug,
        content_hash[:12],
        len(tools),
        "yes" if executor_mcp is not None else "no",
    )
    return True


def load_executor_catalog_from_disk(config: dict[str, Any] | None = None) -> bool:
    """Populate in-memory catalog (+ MCP block) from disk slug when present."""
    cfg = config or load_config()
    if not tools_hook_executor_url(cfg):
        return False
    token = _resolve_executor_token(cfg, allow_prompt=False)
    cache_key = _cache_key_for_config(cfg, token)
    state = _get_state(cache_key)
    with _catalog_lock:
        if state.tools:
            return True
    return _load_catalog_from_disk(cache_key)


def _wait_for_refresh(state: _ExecutorCatalogState) -> None:
    if not state.refresh_in_progress:
        return
    state.refresh_done.wait(timeout=_REFRESH_WAIT_SECONDS)


def _run_background_refresh(
    *,
    config: dict[str, Any],
    token: str | None,
    cache_key: _ExecutorCacheKey,
) -> None:
    """Refresh list + every tool schema; swap only when the full catalog is ready."""
    state = _get_state(cache_key)
    try:
        try:
            logger.info("executor catalog network_fetch slug=%s", cache_key.slug)
            tools, executor_mcp = asyncio.run(
                _fetch_catalog_and_mcp_async(
                    base_url=cache_key.base_url,
                    token=token,
                ),
            )
        except httpx.HTTPError as exc:
            logger.warning("executor background catalog refresh failed: %s", exc)
            return
        except ValueError as exc:
            logger.warning("executor background catalog refresh invalid: %s", exc)
            return

        content_hash = raw_catalog_content_hash(tools)
        _apply_catalog_to_state(
            state,
            tools,
            content_hash=content_hash,
            executor_mcp=executor_mcp,
        )
        write_kwargs: dict[str, Any] = {
            "executor_url": cache_key.base_url,
            "tools": tools,
            "content_hash": content_hash,
        }
        if executor_mcp is not None:
            write_kwargs["executor"] = executor_mcp
        write_disk_catalog(cache_key.slug, **write_kwargs)
        logger.debug(
            "executor catalog refresh completed (%d tools, mcp=%s)",
            len(tools),
            "yes" if executor_mcp is not None else "no",
        )
    finally:
        with _catalog_lock:
            state.refresh_in_progress = False
            state.refresh_done.set()


def _start_background_refresh(
    cfg: dict[str, Any],
    token: str | None,
    cache_key: _ExecutorCacheKey,
    *,
    force: bool,
) -> None:
    state = _get_state(cache_key)
    now = time.monotonic()

    with _catalog_lock:
        if state.refresh_in_progress:
            return
        if not force and state.tools and not _catalog_is_stale(state, now=now):
            return
        state.refresh_in_progress = True
        state.refresh_done.clear()

    thread = threading.Thread(
        target=_run_background_refresh,
        kwargs={"config": cfg, "token": token, "cache_key": cache_key},
        name="cyt-executor-catalog-refresh",
        daemon=True,
    )
    thread.start()


def schedule_executor_catalog_refresh(
    config: dict[str, Any] | None = None,
    *,
    allow_prompt: bool = False,
    force: bool = False,
) -> None:
    """Start a background catalog refresh when stale; never blocks the caller."""
    cfg = config or load_config()
    base_url = tools_hook_executor_url(cfg)
    if not base_url:
        return

    token = _resolve_executor_token(cfg, allow_prompt=allow_prompt)
    cache_key = _cache_key_for_config(cfg, token)
    _start_background_refresh(cfg, token, cache_key, force=force)


def _blocking_network_fetch(
    cfg: dict[str, Any],
    token: str | None,
    cache_key: _ExecutorCacheKey,
) -> list[dict[str, Any]]:
    state = _get_state(cache_key)
    try:
        logger.info("executor catalog network_fetch slug=%s blocking=true", cache_key.slug)
        tools, executor_mcp = asyncio.run(
            _fetch_catalog_and_mcp_async(base_url=cache_key.base_url, token=token),
        )
    except httpx.HTTPError as exc:
        logger.warning("executor tool catalog fetch failed: %s", exc)
        stale = _snapshot_tools(state)
        return stale if stale else []
    except ValueError as exc:
        logger.warning("executor tool catalog response invalid: %s", exc)
        stale = _snapshot_tools(state)
        return stale if stale else []

    content_hash = raw_catalog_content_hash(tools)
    _apply_catalog_to_state(
        state,
        tools,
        content_hash=content_hash,
        executor_mcp=executor_mcp,
    )
    write_kwargs: dict[str, Any] = {
        "executor_url": cache_key.base_url,
        "tools": tools,
        "content_hash": content_hash,
    }
    if executor_mcp is not None:
        write_kwargs["executor"] = executor_mcp
    write_disk_catalog(cache_key.slug, **write_kwargs)
    return copy.deepcopy(tools)


def get_executor_mcp_cache(
    config: dict[str, Any] | None = None,
    *,
    allow_prompt: bool = False,
) -> dict[str, Any] | None:
    """Return in-memory MCP transport cache (``tools_list`` + ``execute_skill``).

    Memory-first: loads from disk when cold, schedules a non-blocking SWR refresh,
    and never calls the live executor API on the request path.
    """
    cfg = config or load_config()
    if not tools_hook_executor_url(cfg):
        return None

    token = _resolve_executor_token(cfg, allow_prompt=allow_prompt)
    cache_key = _cache_key_for_config(cfg, token)
    state = _get_state(cache_key)

    with _catalog_lock:
        has_memory = bool(state.tools) or state.executor_mcp is not None

    if not has_memory:
        _load_catalog_from_disk(cache_key)

    schedule_executor_catalog_refresh(cfg, allow_prompt=allow_prompt, force=False)
    return _snapshot_executor_mcp(state)


def get_executor_catalog(
    config: dict[str, Any] | None = None,
    *,
    allow_prompt: bool = True,
    blocking: bool = False,
    force: bool = False,
) -> list[dict[str, Any]] | None:
    """Unified SWR entrypoint: memory → disk → wait for refresh → block only if both empty."""
    cfg = config or load_config()
    if not tools_hook_executor_url(cfg):
        return None

    token = _resolve_executor_token(cfg, allow_prompt=allow_prompt)
    cache_key = _cache_key_for_config(cfg, token)
    state = _get_state(cache_key)

    with _catalog_lock:
        has_memory = bool(state.tools)

    if not has_memory:
        _load_catalog_from_disk(cache_key)
        with _catalog_lock:
            has_memory = bool(state.tools)

    if not has_memory:
        if state.refresh_in_progress:
            _wait_for_refresh(state)
            snapshot = _snapshot_tools(state)
            if snapshot:
                return snapshot
        return _blocking_network_fetch(cfg, token, cache_key)

    if force and blocking:
        return _blocking_network_fetch(cfg, token, cache_key)

    logger.debug(
        "executor catalog cache_hit slug=%s tool_count=%d",
        cache_key.slug,
        len(state.tools),
    )
    schedule_executor_catalog_refresh(cfg, allow_prompt=allow_prompt, force=force)
    return _snapshot_tools(state)


def load_executor_tools(
    config: dict[str, Any] | None = None,
    *,
    allow_prompt: bool = True,
    blocking: bool = False,
) -> list[dict[str, Any]] | None:
    """Return the executor catalog; non-blocking by default (stale-while-revalidate)."""
    return get_executor_catalog(
        config,
        allow_prompt=allow_prompt,
        blocking=blocking,
        force=False,
    )


def fetch_executor_tools(
    config: dict[str, Any] | None = None,
    *,
    allow_prompt: bool = True,
    blocking: bool = False,
    force: bool = False,
) -> list[dict[str, Any]]:
    """Fetch executor tools; ``blocking=True`` is cache-first unless ``force=True``."""
    result = get_executor_catalog(
        config,
        allow_prompt=allow_prompt,
        blocking=blocking,
        force=force,
    )
    return result if result is not None else []


def executor_catalog_health_snapshot(
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Catalog fields for ``/health``."""
    cfg = config or load_config()
    if not tools_hook_executor_url(cfg):
        return {}

    token = _resolve_executor_token(cfg, allow_prompt=False)
    cache_key = _cache_key_for_config(cfg, token)
    state = _get_state(cache_key)
    with _catalog_lock:
        tool_count = len(state.tools)
        age_seconds = time.monotonic() - state.updated_at if state.updated_at else None
        content_hash = state.catalog_content_hash
        refresh_in_progress = state.refresh_in_progress
        has_mcp = state.executor_mcp is not None
        mcp_tools = 0
        if isinstance(state.executor_mcp, dict):
            tools_list = state.executor_mcp.get("tools_list")
            if isinstance(tools_list, list):
                mcp_tools = len(tools_list)

    payload: dict[str, Any] = {
        "catalog_tool_count": tool_count,
        "refresh_in_progress": refresh_in_progress,
        "executor_mcp_cached": has_mcp,
        "executor_mcp_tools_list_count": mcp_tools,
    }
    if age_seconds is not None:
        payload["catalog_age_seconds"] = round(age_seconds, 1)
    if content_hash:
        payload["catalog_content_hash_prefix"] = content_hash[:12]
    payload["executor_catalog_slug"] = cache_key.slug
    return payload
