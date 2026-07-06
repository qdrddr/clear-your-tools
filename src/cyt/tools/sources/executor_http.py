"""Load tool catalogs from the Executor MCP aggregator HTTP API."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
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


_executor_cache: dict[_ExecutorCacheKey, tuple[float, list[dict[str, Any]]]] = {}


def _token_fingerprint(token: str | None) -> str:
    if not token:
        return ""
    return str(hash(token))


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


async def _fetch_schema_async(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    address: str,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any] | None:
    async with semaphore:
        try:
            response = await client.get(
                f"{base_url}{_SCHEMA_PATH}",
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
    return _normalize_tool(address, description if description is not None else None, input_schema)


async def _fetch_tools_async(
    *,
    base_url: str,
    token: str | None,
    concurrency: int = _DEFAULT_SCHEMA_CONCURRENCY,
) -> list[dict[str, Any]]:
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

        semaphore = asyncio.Semaphore(max(1, concurrency))
        tasks = [
            _fetch_schema_async(
                client,
                base_url=base_url,
                address=address,
                semaphore=semaphore,
            )
            for address, _ in summaries
        ]
        schemas = await asyncio.gather(*tasks)

    tools: list[dict[str, Any]] = []
    for (address, description), schema_tool in zip(summaries, schemas, strict=True):
        if schema_tool is not None:
            tools.append(schema_tool)
            continue
        tools.append(_normalize_tool(address, description, {}))
    return tools


def fetch_executor_tools(
    config: dict[str, Any] | None = None,
    *,
    allow_prompt: bool = True,
) -> list[dict[str, Any]]:
    """Fetch the full executor tool catalog (list + per-tool schema)."""
    cfg = config or load_config()
    base_url = tools_hook_executor_url(cfg)
    if not base_url:
        return []

    token = _resolve_executor_token(cfg, allow_prompt=allow_prompt)
    cache_key = _ExecutorCacheKey(base_url, _token_fingerprint(token))
    now = time.monotonic()
    cached = _executor_cache.get(cache_key)
    if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    try:
        tools = asyncio.run(_fetch_tools_async(base_url=base_url, token=token))
    except httpx.HTTPError as exc:
        logger.warning("executor tool list fetch failed: %s", exc)
        return []
    except ValueError as exc:
        logger.warning("executor tool list response invalid: %s", exc)
        return []

    _executor_cache[cache_key] = (now, tools)
    return tools


def load_executor_tools(
    config: dict[str, Any] | None = None,
    *,
    allow_prompt: bool = True,
) -> list[dict[str, Any]] | None:
    """Load executor tools; return None when executor_url is unset."""
    cfg = config or load_config()
    if not tools_hook_executor_url(cfg):
        return None
    return fetch_executor_tools(cfg, allow_prompt=allow_prompt)
