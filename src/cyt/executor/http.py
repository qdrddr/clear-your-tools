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

from cyt.executor.catalog_disk import (
    normalize_executor_url_slug,
    raw_catalog_content_hash,
    read_disk_catalog,
    write_disk_catalog,
)
from cyt.executor.connection_health import (
    ConnectionKey,
    clear_connection_health_cache,
    connection_health_snapshot_fields,
    filter_catalog_by_health,
    merge_tool_metadata,
)
from cyt.executor.runtime import (
    load_config,
    resolve_credential,
    tools_hook_executor_token_var,
    tools_hook_executor_url,
    uses_executor_tool_catalog,
)

logger = logging.getLogger(__name__)

_DEFAULT_SCHEMA_CONCURRENCY = 4
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


_catalog_lock = threading.Lock()
_catalog_states: dict[_ExecutorCacheKey, _ExecutorCatalogState] = {}


def clear_executor_catalog_cache() -> None:
    """Reset in-process executor catalog state (for tests)."""
    with _catalog_lock:
        _catalog_states.clear()
    clear_connection_health_cache()
    from cyt.executor.cache_scheduler import clear_executor_cache_schedulers

    clear_executor_cache_schedulers()


def _executor_runtime_active(config: dict[str, Any]) -> bool:
    """True when hook injection loads tools from the live Executor catalog."""
    return uses_executor_tool_catalog(config)


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
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tool: dict[str, Any] = {"name": address}
    if description:
        tool["description"] = str(description)
    if isinstance(input_schema, dict):
        tool["input_schema"] = input_schema
    merge_tool_metadata(tool, metadata)
    return tool


def _list_item_metadata(item: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for key in ("owner", "integration", "connection", "static"):
        if key in item:
            metadata[key] = item[key]
    return metadata


def _summaries_to_stub_tools(
    summaries: list[tuple[str, str | None, dict[str, Any]]],
) -> list[dict[str, Any]]:
    return [
        _normalize_tool(address, description, {}, metadata)
        for address, description, metadata in summaries
    ]


def merge_list_stubs_into_catalog(
    state: _ExecutorCatalogState,
    tools: list[dict[str, Any]],
) -> None:
    """Merge tier-1 stubs or tier-2 schema tools by address."""
    by_name = {str(tool.get("name") or ""): tool for tool in state.tools}
    for tool in tools:
        name = str(tool.get("name") or "")
        if not name:
            continue
        existing = by_name.get(name)
        if existing is None:
            by_name[name] = copy.deepcopy(tool)
            continue
        if tool.get("input_schema"):
            existing["input_schema"] = copy.deepcopy(tool["input_schema"])
        if tool.get("description") and not existing.get("description"):
            existing["description"] = tool["description"]
        for key in ("owner", "integration", "connection", "static"):
            if key in tool and tool[key] is not None:
                existing[key] = tool[key]
    with _catalog_lock:
        state.tools = list(by_name.values())
        state.updated_at = time.monotonic()
        state.catalog_content_hash = raw_catalog_content_hash(state.tools)


def evict_schemas_for_connections(
    state: _ExecutorCatalogState,
    keys: set[ConnectionKey],
) -> None:
    """Strip ``input_schema`` from tools on newly ineligible connections."""
    if not keys:
        return
    from cyt.executor.connection_health import connection_key_from_tool

    with _catalog_lock:
        for tool in state.tools:
            key = connection_key_from_tool(tool)
            if key is not None and key in keys and "input_schema" in tool:
                del tool["input_schema"]
        state.catalog_content_hash = raw_catalog_content_hash(state.tools)
        state.updated_at = time.monotonic()


async def _fetch_list_async(
    *,
    base_url: str,
    token: str | None,
) -> list[tuple[str, str | None, dict[str, Any]]]:
    headers = _auth_headers(token)
    timeout = httpx.Timeout(60.0, connect=10.0)
    async with httpx.AsyncClient(headers=headers, timeout=timeout) as client:
        response = await client.get(
            f"{base_url}{_LIST_PATH}",
            params={"includeBlocked": "false"},
        )
        response.raise_for_status()
        listed = response.json()
    if not isinstance(listed, list):
        raise ValueError("executor /api/tools response must be a JSON array")

    summaries: list[tuple[str, str | None, dict[str, Any]]] = []
    for item in listed:
        if not isinstance(item, dict):
            continue
        address = str(item.get("address") or "").strip()
        if not address:
            continue
        description = item.get("description")
        desc_text = str(description) if description is not None else None
        summaries.append((address, desc_text, _list_item_metadata(item)))
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
    summaries: list[tuple[str, str | None, dict[str, Any]]],
    concurrency: int = _DEFAULT_SCHEMA_CONCURRENCY,
) -> list[dict[str, Any]]:
    if not summaries:
        return []

    addresses = [address for address, _, _ in summaries]
    descriptions = {address: description for address, description, _ in summaries}
    metadata_by_address = {address: metadata for address, _, metadata in summaries}

    headers = _auth_headers(token)
    timeout = httpx.Timeout(60.0, connect=10.0)
    semaphore = asyncio.Semaphore(max(1, concurrency))
    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=timeout) as client:
        tasks = [
            _fetch_schema_async(client, address=address, semaphore=semaphore)
            for address in addresses
        ]
        schemas = await asyncio.gather(*tasks)

    tools: list[dict[str, Any]] = []
    for address, schema_tool in zip(addresses, schemas, strict=True):
        metadata = metadata_by_address.get(address, {})
        if schema_tool is not None:
            merge_tool_metadata(schema_tool, metadata)
            tools.append(schema_tool)
            continue
        tools.append(
            _normalize_tool(address, descriptions.get(address), {}, metadata),
        )
    return tools


async def _fetch_full_catalog_async(
    *,
    base_url: str,
    token: str | None,
    slug: str,
    config: dict[str, Any] | None = None,
    concurrency: int = _DEFAULT_SCHEMA_CONCURRENCY,
) -> list[dict[str, Any]]:
    from cyt.executor.connection_health import filter_summaries_for_schema_fetch

    summaries = await _fetch_list_async(base_url=base_url, token=token)
    eligible = filter_summaries_for_schema_fetch(
        summaries,
        slug,
        config=config or load_config(),
    )
    return await _fetch_schemas_async(
        base_url=base_url,
        token=token,
        summaries=eligible,
        concurrency=concurrency,
    )


async def _fetch_catalog_and_mcp_async(
    *,
    base_url: str,
    token: str | None,
    slug: str,
    config: dict[str, Any] | None = None,
    concurrency: int = _DEFAULT_SCHEMA_CONCURRENCY,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Blocking bootstrap: tier-1 list + eligible tier-2 schemas + MCP transport cache."""
    from cyt.executor.mcp import fetch_executor_mcp_cache_async

    catalog_task = asyncio.create_task(
        _fetch_full_catalog_async(
            base_url=base_url,
            token=token,
            slug=slug,
            config=config,
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


def _write_catalog_disk(
    cache_key: _ExecutorCacheKey,
    *,
    tools: list[dict[str, Any]],
    executor_mcp: dict[str, Any] | None = None,
    connections_health: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> None:
    content_hash = raw_catalog_content_hash(tools)
    write_kwargs: dict[str, Any] = {
        "executor_url": cache_key.base_url,
        "tools": tools,
        "content_hash": content_hash,
    }
    if executor_mcp is not None:
        write_kwargs["executor"] = executor_mcp
    if connections_health is not None:
        write_kwargs["connections_health"] = connections_health
    write_disk_catalog(cache_key.slug, **write_kwargs)


def _load_catalog_from_disk(
    cache_key: _ExecutorCacheKey,
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
    if not _executor_runtime_active(cfg):
        return False
    if not tools_hook_executor_url(cfg):
        return False
    token = _resolve_executor_token(cfg, allow_prompt=False)
    cache_key = _cache_key_for_config(cfg, token)
    state = _get_state(cache_key)
    with _catalog_lock:
        if state.tools:
            return True
    return _load_catalog_from_disk(cache_key, config=cfg)


def _ensure_scheduler_started(cfg: dict[str, Any], *, allow_prompt: bool) -> None:
    from cyt.executor.cache_scheduler import start_executor_cache_scheduler

    start_executor_cache_scheduler(cfg, allow_prompt=allow_prompt)


def schedule_executor_catalog_refresh(
    config: dict[str, Any] | None = None,
    *,
    allow_prompt: bool = False,
    force: bool = False,
) -> None:
    """Ensure background scheduler is running; optionally force catalog refresh."""
    from cyt.executor.cache_scheduler import schedule_executor_catalog_refresh as _schedule

    _schedule(config, allow_prompt=allow_prompt, force=force)


def _blocking_network_fetch(
    cfg: dict[str, Any],
    token: str | None,
    cache_key: _ExecutorCacheKey,
    *,
    apply_health_filter: bool = True,
) -> list[dict[str, Any]]:
    from cyt.executor.connection_health import refresh_connection_health_async

    state = _get_state(cache_key)
    try:
        logger.info("executor catalog network_fetch slug=%s blocking=true", cache_key.slug)
        tools, executor_mcp = asyncio.run(
            _fetch_catalog_and_mcp_async(
                base_url=cache_key.base_url,
                token=token,
                slug=cache_key.slug,
                config=cfg,
            ),
        )
        try:
            asyncio.run(
                refresh_connection_health_async(
                    base_url=cache_key.base_url,
                    token=token,
                    slug=cache_key.slug,
                    config=cfg,
                ),
            )
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("executor bootstrap health refresh failed: %s", exc)
    except httpx.HTTPError as exc:
        logger.warning("executor tool catalog fetch failed: %s", exc)
        stale = _snapshot_tools(state)
        return _return_catalog(
            stale,
            cache_key,
            apply_health_filter=apply_health_filter,
            config=cfg,
        )
    except ValueError as exc:
        logger.warning("executor tool catalog response invalid: %s", exc)
        stale = _snapshot_tools(state)
        return _return_catalog(
            stale,
            cache_key,
            apply_health_filter=apply_health_filter,
            config=cfg,
        )

    _apply_catalog_to_state(
        state,
        tools,
        content_hash=raw_catalog_content_hash(tools),
        executor_mcp=executor_mcp,
    )
    _write_catalog_disk(
        cache_key,
        tools=tools,
        executor_mcp=executor_mcp,
        config=cfg,
    )
    _ensure_scheduler_started(cfg, allow_prompt=False)
    return _return_catalog(
        copy.deepcopy(tools),
        cache_key,
        apply_health_filter=apply_health_filter,
        config=cfg,
    )


def _return_catalog(
    tools: list[dict[str, Any]],
    cache_key: _ExecutorCacheKey,
    *,
    apply_health_filter: bool,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not tools:
        return []
    if not apply_health_filter:
        return tools
    return filter_catalog_by_health(tools, cache_key.slug, config=config)


def get_executor_mcp_cache(
    config: dict[str, Any] | None = None,
    *,
    allow_prompt: bool = False,
) -> dict[str, Any] | None:
    """Return in-memory MCP transport cache (``tools_list`` + ``execute_skill``).

    Memory-first: loads from disk when cold, starts background scheduler,
    and never calls the live executor API on the request path.
    """
    cfg = config or load_config()
    if not _executor_runtime_active(cfg):
        return None
    if not tools_hook_executor_url(cfg):
        return None

    token = _resolve_executor_token(cfg, allow_prompt=allow_prompt)
    cache_key = _cache_key_for_config(cfg, token)
    state = _get_state(cache_key)

    with _catalog_lock:
        has_memory = bool(state.tools) or state.executor_mcp is not None

    if not has_memory:
        _load_catalog_from_disk(cache_key, config=cfg)

    _ensure_scheduler_started(cfg, allow_prompt=allow_prompt)
    return _snapshot_executor_mcp(state)


def _get_executor_catalog_impl(
    cfg: dict[str, Any],
    *,
    allow_prompt: bool,
    blocking: bool,
    force: bool,
    apply_health_filter: bool = True,
) -> list[dict[str, Any]] | None:
    if not tools_hook_executor_url(cfg):
        return None

    token = _resolve_executor_token(cfg, allow_prompt=allow_prompt)
    cache_key = _cache_key_for_config(cfg, token)
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
                token,
                cache_key,
                apply_health_filter=apply_health_filter,
            )
        _ensure_scheduler_started(cfg, allow_prompt=allow_prompt)
        return _return_catalog([], cache_key, apply_health_filter=apply_health_filter, config=cfg)

    if force and blocking:
        return _blocking_network_fetch(
            cfg,
            token,
            cache_key,
            apply_health_filter=apply_health_filter,
        )

    logger.debug(
        "executor catalog cache_hit slug=%s tool_count=%d",
        cache_key.slug,
        len(state.tools),
    )
    _ensure_scheduler_started(cfg, allow_prompt=allow_prompt)
    return _return_catalog(
        _snapshot_tools(state),
        cache_key,
        apply_health_filter=apply_health_filter,
        config=cfg,
    )


def get_executor_catalog(
    config: dict[str, Any] | None = None,
    *,
    allow_prompt: bool = True,
    blocking: bool = False,
    force: bool = False,
) -> list[dict[str, Any]] | None:
    """Unified SWR entrypoint: memory snapshot only on hook path (never blocks on refresh)."""
    cfg = config or load_config()
    if not _executor_runtime_active(cfg):
        return None
    return _get_executor_catalog_impl(
        cfg,
        allow_prompt=allow_prompt,
        blocking=blocking,
        force=force,
    )


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


def fetch_executor_tools_for_cli(
    config: dict[str, Any] | None = None,
    *,
    allow_prompt: bool = True,
    blocking: bool = False,
    force: bool = False,
) -> list[dict[str, Any]]:
    """Fetch executor tools for ``cyt executor save``; ignores ``inject_via``."""
    cfg = config or load_config()
    result = _get_executor_catalog_impl(
        cfg,
        allow_prompt=allow_prompt,
        blocking=blocking,
        force=force,
        apply_health_filter=False,
    )
    return result if result is not None else []


def executor_catalog_health_snapshot(
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Catalog fields for ``/health``."""
    cfg = config or load_config()
    if not _executor_runtime_active(cfg):
        return {}
    if not tools_hook_executor_url(cfg):
        return {}

    token = _resolve_executor_token(cfg, allow_prompt=False)
    cache_key = _cache_key_for_config(cfg, token)
    state = _get_state(cache_key)
    with _catalog_lock:
        tool_count = len(state.tools)
        age_seconds = time.monotonic() - state.updated_at if state.updated_at else None
        content_hash = state.catalog_content_hash
        has_mcp = state.executor_mcp is not None
        mcp_tools = 0
        if isinstance(state.executor_mcp, dict):
            tools_list = state.executor_mcp.get("tools_list")
            if isinstance(tools_list, list):
                mcp_tools = len(tools_list)

    payload: dict[str, Any] = {
        "catalog_tool_count": tool_count,
        "executor_mcp_cached": has_mcp,
        "executor_mcp_tools_list_count": mcp_tools,
    }
    payload.update(connection_health_snapshot_fields(cache_key.slug, config=cfg))
    if age_seconds is not None:
        payload["catalog_age_seconds"] = round(age_seconds, 1)
    if content_hash:
        payload["catalog_content_hash_prefix"] = content_hash[:12]
    payload["executor_catalog_slug"] = cache_key.slug
    return payload
