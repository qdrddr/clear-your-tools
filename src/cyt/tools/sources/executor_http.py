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

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 60.0
_DEFAULT_SCHEMA_CONCURRENCY = 16
_LIST_PATH = "/api/tools"
_SCHEMA_PATH = "/api/tools/schema"


@dataclass(frozen=True)
class _ExecutorCacheKey:
    base_url: str
    token_fingerprint: str


@dataclass
class _ExecutorCatalogState:
    tools: list[dict[str, Any]] = field(default_factory=list)
    updated_at: float = 0.0
    refresh_in_progress: bool = False


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
    return _ExecutorCacheKey(
        tools_hook_executor_url(config),
        _token_fingerprint(token),
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


def _catalog_is_stale(state: _ExecutorCatalogState, *, now: float) -> bool:
    if not state.tools:
        return True
    return now - state.updated_at >= _CACHE_TTL_SECONDS


def _run_background_refresh(
    *,
    config: dict[str, Any],
    token: str | None,
    cache_key: _ExecutorCacheKey,
) -> None:
    """Refresh list + every tool schema; swap only when the full catalog is ready."""
    state = _get_state(cache_key)
    try:
        tools = asyncio.run(
            _fetch_full_catalog_async(
                base_url=cache_key.base_url,
                token=token,
            ),
        )
    except httpx.HTTPError as exc:
        logger.warning("executor background catalog refresh failed: %s", exc)
        with _catalog_lock:
            state.refresh_in_progress = False
        return
    except ValueError as exc:
        logger.warning("executor background catalog refresh invalid: %s", exc)
        with _catalog_lock:
            state.refresh_in_progress = False
        return

    with _catalog_lock:
        state.tools = tools
        state.updated_at = time.monotonic()
        state.refresh_in_progress = False
    logger.debug("executor catalog refresh completed (%d tools)", len(tools))


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
    state = _get_state(cache_key)
    now = time.monotonic()

    with _catalog_lock:
        if state.refresh_in_progress:
            return
        if not force and state.tools and not _catalog_is_stale(state, now=now):
            return
        state.refresh_in_progress = True

    thread = threading.Thread(
        target=_run_background_refresh,
        kwargs={"config": cfg, "token": token, "cache_key": cache_key},
        name="cyt-executor-catalog-refresh",
        daemon=True,
    )
    thread.start()


def load_executor_tools(
    config: dict[str, Any] | None = None,
    *,
    allow_prompt: bool = True,
    blocking: bool = False,
) -> list[dict[str, Any]] | None:
    """Return the executor catalog; non-blocking by default (stale-while-revalidate)."""
    cfg = config or load_config()
    if not tools_hook_executor_url(cfg):
        return None

    if blocking:
        return fetch_executor_tools(cfg, allow_prompt=allow_prompt, blocking=True)

    token = _resolve_executor_token(cfg, allow_prompt=allow_prompt)
    cache_key = _cache_key_for_config(cfg, token)
    state = _get_state(cache_key)
    schedule_executor_catalog_refresh(cfg, allow_prompt=False, force=False)
    return _snapshot_tools(state)


def fetch_executor_tools(
    config: dict[str, Any] | None = None,
    *,
    allow_prompt: bool = True,
    blocking: bool = False,
) -> list[dict[str, Any]]:
    """Fetch executor tools; use ``blocking=True`` for a synchronous full refresh."""
    cfg = config or load_config()
    base_url = tools_hook_executor_url(cfg)
    if not base_url:
        return []

    token = _resolve_executor_token(cfg, allow_prompt=allow_prompt)
    cache_key = _cache_key_for_config(cfg, token)
    state = _get_state(cache_key)

    if not blocking:
        schedule_executor_catalog_refresh(cfg, allow_prompt=allow_prompt, force=False)
        return _snapshot_tools(state)

    try:
        tools = asyncio.run(
            _fetch_full_catalog_async(base_url=base_url, token=token),
        )
    except httpx.HTTPError as exc:
        logger.warning("executor tool catalog fetch failed: %s", exc)
        stale = _snapshot_tools(state)
        return stale if stale else []
    except ValueError as exc:
        logger.warning("executor tool catalog response invalid: %s", exc)
        stale = _snapshot_tools(state)
        return stale if stale else []

    with _catalog_lock:
        state.tools = tools
        state.updated_at = time.monotonic()
    return copy.deepcopy(tools)
