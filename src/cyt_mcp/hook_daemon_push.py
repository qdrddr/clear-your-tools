"""Push cyt-mcp tool catalogs to the hook daemon (stdlib-only, non-blocking)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from dataclasses import dataclass
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastmcp import FastMCP

from cyt.hook.daemon_client import resolve_hook_path
from cyt_mcp.catalog import catalog_payload
from cyt_mcp.config import AggregatorConfig, catalog_layer_for_scope
from cyt_mcp.config_holder import ConfigHolder
from cyt_mcp.runtime_cache import RuntimeToolCache
from cyt_mcp.tool_list_notify import ToolListChangedMiddleware, notify_all_sessions_list_changed

logger = logging.getLogger(__name__)

CatalogScope = Literal["workspace"]

REGISTER_PATH = "/hook/catalog/register"
DEREGISTER_PATH = "/hook/catalog/deregister"
PUSH_TIMEOUT_SECONDS = 2.0

_RETRY_DELAYS_SECONDS = (1.0, 2.0, 5.0, 10.0)

_push_lock = threading.Lock()
_push_tasks: dict[str, asyncio.Task[None]] = {}
_immediate_push_tasks: set[asyncio.Task[None]] = set()
_last_success_hash: dict[str, str] = {}
_last_permissions_revision: dict[str, int] = {}
_instance_id = f"pid:{os.getpid()}"


@dataclass
class PushContext:
    config_holder: ConfigHolder
    cache: RuntimeToolCache
    server: FastMCP
    list_changed_middleware: ToolListChangedMiddleware | None = None


_push_contexts: dict[str, PushContext] = {}


def register_push_context(context: PushContext) -> None:
    key = _instance_key(context.config_holder.config)
    _push_contexts[key] = context


def unregister_push_context(config: AggregatorConfig) -> None:
    key = _instance_key(config)
    _push_contexts.pop(key, None)


def _instance_key(config: AggregatorConfig) -> str:
    ws = str(config.workspace_root or "")
    layer = catalog_layer_for_scope(config.catalog_scope)
    return f"{config.agent}:workspace:{ws}:{layer}"


def _can_push_to_registry(config: AggregatorConfig) -> bool:
    if config.workspace_root is None:
        logger.debug("cyt-mcp catalog push skipped: workspace_root not resolved")
        return False
    try:
        resolved = config.workspace_root.expanduser().resolve()
    except OSError:
        return False
    return resolved.is_dir()


def resolve_hook_register_url() -> str | None:
    return resolve_hook_path(REGISTER_PATH)


def _build_register_payload(
    config: AggregatorConfig,
    cache: RuntimeToolCache,
    *,
    include_tools: bool,
) -> dict[str, Any]:
    payload_data = catalog_payload(
        cache,
        agent=config.agent,
        server_origins=dict(config.server_origins),
    )
    tools = payload_data.get("tools")
    if not isinstance(tools, list):
        tools = []
    from cyt_mcp.catalog import catalog_tools_content_hash

    content_hash = catalog_tools_content_hash(tools)
    body: dict[str, Any] = {
        "agent": config.agent,
        "scope": "workspace",
        "workspace_root": str(config.workspace_root) if config.workspace_root is not None else None,
        "catalog_layer": catalog_layer_for_scope(config.catalog_scope),
        "instance_id": _instance_id,
        "content_hash": content_hash,
    }
    if include_tools:
        body["tools"] = tools
    return body


def _catalog_hash(cache: RuntimeToolCache) -> str:
    from cyt_mcp.catalog import catalog_tools_content_hash

    return catalog_tools_content_hash(cache.snapshot())


def _post_json(url: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any] | None]:
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=PUSH_TIMEOUT_SECONDS) as response:
            code = response.getcode()
            status = int(code) if isinstance(code, int) else 200
            body_text = response.read().decode("utf-8", errors="replace").strip()
            if not body_text:
                return status, None
            try:
                parsed = json.loads(body_text)
            except json.JSONDecodeError:
                return status, None
            return status, parsed if isinstance(parsed, dict) else None
    except HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace").strip()
        error_body: dict[str, Any] | None = None
        if body_text:
            try:
                raw = json.loads(body_text)
                error_body = raw if isinstance(raw, dict) else None
            except json.JSONDecodeError:
                error_body = None
        return int(exc.code), error_body
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        logger.debug("cyt-mcp catalog push failed: %s", exc)
        return 0, None


def _permissions_revision_from_response(response: dict[str, Any] | None) -> int:
    if response is None:
        return 0
    raw = response.get("permissions_revision")
    if raw is None:
        return 0
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


async def _refresh_permissions_catalog(context: PushContext, *, key: str) -> bool:
    """Reload deny overlays and rebuild runtime cache. Returns True when clients should refresh."""
    old_hash = _catalog_hash(context.cache)
    deny_before = tuple(context.config_holder.mcp_deny)
    context.config_holder.reload_mcp_deny()
    deny_after = tuple(context.config_holder.mcp_deny)
    from cyt_mcp.catalog_build import reapply_deny_overlays, refresh_catalog_cache

    if len(context.cache.snapshot()) == 0:
        from cyt_mcp.catalog_build import hydrate_runtime_cache

        hydrate_runtime_cache(context.cache, context.config_holder.config)
    if len(context.cache.snapshot()) == 0:
        await refresh_catalog_cache(
            context.server,
            context.cache,
            context.config_holder.config,
            skip_push=True,
        )
    elif deny_before != deny_after:
        reapply_deny_overlays(context.cache, context.config_holder.config)
    new_hash = _catalog_hash(context.cache)
    _last_success_hash.pop(key, None)
    return new_hash != old_hash or deny_before != deny_after


def _refresh_permissions_catalog_sync(context: PushContext, *, key: str) -> bool:
    """Sync variant for background push threads without a running event loop."""
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_refresh_permissions_catalog(context, key=key))
    raise RuntimeError("_refresh_permissions_catalog_sync requires no running event loop")


async def _maybe_reload_permissions(
    *,
    key: str,
    revision: int,
    context: PushContext | None,
) -> None:
    last = _last_permissions_revision.get(key, 0)
    if revision <= last:
        return
    _last_permissions_revision[key] = revision
    if context is None:
        return

    should_notify = await _refresh_permissions_catalog(context, key=key)
    if context.list_changed_middleware is not None and should_notify:
        await notify_all_sessions_list_changed(context.list_changed_middleware)


def _push_hash_only(
    url: str,
    config: AggregatorConfig,
    cache: RuntimeToolCache,
) -> tuple[int | None, dict[str, Any] | None]:
    """Post hash-only payload; return HTTP status, or None when a full resend is needed."""
    body = _build_register_payload(config, cache, include_tools=False)
    status, response = _post_json(url, body)
    if status == 404:
        return None, response
    return status, response


def _push_full(
    url: str,
    config: AggregatorConfig,
    cache: RuntimeToolCache,
    *,
    key: str,
) -> tuple[bool, dict[str, Any] | None]:
    body = _build_register_payload(config, cache, include_tools=True)
    status, response = _post_json(url, body)
    if status not in {200, 204}:
        return False, response
    tools = body.get("tools")
    if isinstance(tools, list):
        from cyt_mcp.catalog import catalog_tools_content_hash

        _last_success_hash[key] = catalog_tools_content_hash(tools)
    return True, response


def _push_once(config: AggregatorConfig, cache: RuntimeToolCache) -> tuple[bool, int]:
    if not _can_push_to_registry(config):
        return False, 0
    url = resolve_hook_register_url()
    if not url:
        return False, 0

    key = _instance_key(config)
    last_hash = _last_success_hash.get(key)
    payload_data = catalog_payload(
        cache,
        agent=config.agent,
        server_origins=dict(config.server_origins),
    )
    tools = payload_data.get("tools")
    if not isinstance(tools, list):
        tools = []
    from cyt_mcp.catalog import catalog_tools_content_hash

    content_hash = catalog_tools_content_hash(tools)

    if last_hash == content_hash:
        status, response = _push_hash_only(url, config, cache)
        if status == 204:
            return True, _permissions_revision_from_response(response)
        if status == 200:
            return True, _permissions_revision_from_response(response)
        if status == 0:
            return False, 0
        if status is None:
            ok, full_response = _push_full(url, config, cache, key=key)
            return ok, _permissions_revision_from_response(full_response)
        return False, 0

    ok, response = _push_full(url, config, cache, key=key)
    return ok, _permissions_revision_from_response(response)


async def _retry_push_loop(context: PushContext) -> None:
    config = context.config_holder.config
    cache = context.cache
    key = _instance_key(config)
    delay_index = 0
    while True:
        try:
            success, revision = await asyncio.to_thread(_push_once, config, cache)
            if success and revision:
                await _maybe_reload_permissions(key=key, revision=revision, context=context)
        except Exception as exc:
            logger.debug("cyt-mcp catalog push loop error: %s", exc)
            success = False
        if success:
            delay_index = 0
            await asyncio.sleep(10.0)
            continue
        delay = _RETRY_DELAYS_SECONDS[min(delay_index, len(_RETRY_DELAYS_SECONDS) - 1)]
        delay_index = min(delay_index + 1, len(_RETRY_DELAYS_SECONDS) - 1)
        await asyncio.sleep(delay)


def schedule_catalog_push(
    cache: RuntimeToolCache,
    config: AggregatorConfig,
    *,
    skip_push: bool = False,
) -> None:
    """Fire-and-forget background push to hook daemon registry."""
    if skip_push or not _can_push_to_registry(config):
        return
    context = _push_contexts.get(_instance_key(config))
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        thread = threading.Thread(
            target=_push_sync_with_retry,
            args=(config, cache),
            name="cyt-mcp-catalog-push",
            daemon=True,
        )
        thread.start()
        return

    key = _instance_key(config)
    with _push_lock:
        existing = _push_tasks.get(key)
        if existing is not None and not existing.done():
            if context is not None:
                immediate_task = asyncio.create_task(
                    _push_immediate(context),
                    name="cyt-mcp-catalog-push-immediate",
                )
            else:
                immediate_task = asyncio.create_task(
                    _push_immediate_legacy(config, cache),
                    name="cyt-mcp-catalog-push-immediate",
                )
            _immediate_push_tasks.add(immediate_task)
            immediate_task.add_done_callback(_immediate_push_tasks.discard)
            return
        if context is not None:
            task = loop.create_task(
                _retry_push_loop(context),
                name=f"cyt-mcp-catalog-push-{key}",
            )
        else:
            task = loop.create_task(
                _retry_push_loop_legacy(config, cache),
                name=f"cyt-mcp-catalog-push-{key}",
            )
        _push_tasks[key] = task


async def _push_immediate(context: PushContext) -> None:
    config = context.config_holder.config
    success, revision = await asyncio.to_thread(_push_once, config, context.cache)
    if success and revision:
        key = _instance_key(config)
        await _maybe_reload_permissions(key=key, revision=revision, context=context)


async def _retry_push_loop_legacy(config: AggregatorConfig, cache: RuntimeToolCache) -> None:
    key = _instance_key(config)
    delay_index = 0
    while True:
        try:
            success, revision = await asyncio.to_thread(_push_once, config, cache)
            if success and revision:
                await _maybe_reload_permissions(key=key, revision=revision, context=None)
        except Exception as exc:
            logger.debug("cyt-mcp catalog push loop error: %s", exc)
            success = False
        if success:
            delay_index = 0
            await asyncio.sleep(10.0)
            continue
        delay = _RETRY_DELAYS_SECONDS[min(delay_index, len(_RETRY_DELAYS_SECONDS) - 1)]
        delay_index = min(delay_index + 1, len(_RETRY_DELAYS_SECONDS) - 1)
        await asyncio.sleep(delay)


async def _push_immediate_legacy(config: AggregatorConfig, cache: RuntimeToolCache) -> None:
    key = _instance_key(config)
    success, revision = await asyncio.to_thread(_push_once, config, cache)
    if success and revision:
        await _maybe_reload_permissions(key=key, revision=revision, context=None)


def _push_sync_with_retry(config: AggregatorConfig, cache: RuntimeToolCache) -> None:
    key = _instance_key(config)
    delay_index = 0
    while True:
        success, revision = _push_once(config, cache)
        if success and revision:
            last = _last_permissions_revision.get(key, 0)
            if revision > last:
                _last_permissions_revision[key] = revision
                context = _push_contexts.get(key)
                if context is not None:
                    should_notify = _refresh_permissions_catalog_sync(context, key=key)
                    if should_notify and context.list_changed_middleware is not None:
                        import asyncio

                        try:
                            asyncio.run(
                                notify_all_sessions_list_changed(
                                    context.list_changed_middleware,
                                ),
                            )
                        except Exception as exc:
                            logger.warning(
                                "cyt-mcp: sync permissions reload list_changed failed: %s",
                                exc,
                            )
        if success:
            return
        delay = _RETRY_DELAYS_SECONDS[min(delay_index, len(_RETRY_DELAYS_SECONDS) - 1)]
        delay_index = min(delay_index + 1, len(_RETRY_DELAYS_SECONDS) - 1)
        import time

        time.sleep(delay)


def deregister_catalog_push(config: AggregatorConfig) -> None:
    url = resolve_hook_register_url()
    if not url:
        return
    deregister_url = url.replace(REGISTER_PATH, DEREGISTER_PATH)
    body = {
        "agent": config.agent,
        "scope": "workspace",
        "workspace_root": str(config.workspace_root) if config.workspace_root is not None else None,
        "catalog_layer": catalog_layer_for_scope(config.catalog_scope),
        "instance_id": _instance_id,
    }
    try:
        _post_json(deregister_url, body)
    except Exception as exc:
        logger.debug("cyt-mcp catalog deregister failed: %s", exc)

    key = _instance_key(config)
    with _push_lock:
        _last_success_hash.pop(key, None)
        _last_permissions_revision.pop(key, None)
        task = _push_tasks.pop(key, None)
        if task is not None and not task.done():
            task.cancel()
    unregister_push_context(config)
