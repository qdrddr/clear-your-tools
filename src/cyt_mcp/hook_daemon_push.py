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

from cyt_mcp.catalog import catalog_payload
from cyt_mcp.config import AggregatorConfig
from cyt_mcp.config_holder import ConfigHolder
from cyt_mcp.runtime_cache import RuntimeToolCache
from cyt_mcp.tool_list_notify import ToolListChangedMiddleware, notify_all_sessions_list_changed

logger = logging.getLogger(__name__)

CatalogScope = Literal["workspace"]

LOCAL_HOST = "127.0.0.1"
DEFAULT_HOOK_PORT = 8834
REGISTER_PATH = "/hook/catalog/register"
DEREGISTER_PATH = "/hook/catalog/deregister"
HEALTH_TIMEOUT_SECONDS = 1.5
PUSH_TIMEOUT_SECONDS = 2.0
HOOK_DAEMON_PIDFILE = os.path.expanduser("~/.config/cyt/pid.json")
LEGACY_HOOK_DAEMON_PIDFILE = os.path.expanduser("~/.config/cyt/hook-daemon.json")
OWNER_HOOK_DAEMON = "cyt-hook-daemon"
CYT_HOOK_URL_ENV = "CYT_HOOK_URL"

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
    return f"{config.agent}:workspace:{ws}"


def _can_push_to_registry(config: AggregatorConfig) -> bool:
    if config.workspace_root is None:
        logger.debug("cyt-mcp catalog push skipped: workspace_root not resolved")
        return False
    try:
        resolved = config.workspace_root.expanduser().resolve()
    except OSError:
        return False
    return resolved.is_dir()


def _read_hook_daemon_entries() -> list[dict[str, Any]]:
    for path in (HOOK_DAEMON_PIDFILE, LEGACY_HOOK_DAEMON_PIDFILE):
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            continue
        if isinstance(payload, list):
            entries = [entry for entry in payload if isinstance(entry, dict)]
        elif isinstance(payload, dict):
            entries = [payload]
        else:
            entries = []
        hook_entries = [
            entry
            for entry in entries
            if entry.get("owner") == OWNER_HOOK_DAEMON or entry.get("hook_url") is not None
        ]
        if hook_entries:
            return hook_entries
    return []


def _fetch_cyt_health(port: int) -> dict[str, Any] | None:
    url = f"http://{LOCAL_HOST}:{port}/health"
    try:
        with urlopen(url, timeout=HEALTH_TIMEOUT_SECONDS) as response:
            code = response.getcode()
            if not isinstance(code, int) or code != 200:
                return None
            payload = json.loads(response.read())
            return payload if isinstance(payload, dict) else None
    except (URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None


def _is_hook_server(health: dict[str, Any] | None) -> bool:
    return (
        isinstance(health, dict)
        and health.get("name") == "cyt"
        and health.get("status") == "ok"
        and health.get("hook") is True
    )


def resolve_hook_register_url() -> str | None:
    env_url = os.environ.get(CYT_HOOK_URL_ENV, "").strip()
    if env_url:
        base = env_url.rstrip("/")
        if base.endswith(("/hook/connect", "/hook/inject")):
            base = base.rsplit("/", 2)[0]
        return f"{base}{REGISTER_PATH}"

    entries = _read_hook_daemon_entries()
    ports: list[int] = []
    for entry in entries:
        port_raw = entry.get("port")
        if port_raw is None:
            continue
        try:
            port = int(port_raw)
        except (TypeError, ValueError):
            continue
        if port > 0:
            ports.append(port)
    if not ports:
        ports = [DEFAULT_HOOK_PORT]

    for port in sorted(set(ports)):
        if _is_hook_server(_fetch_cyt_health(port)):
            return f"http://{LOCAL_HOST}:{port}{REGISTER_PATH}"
    return None


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

    old_hash = _catalog_hash(context.cache)
    context.config_holder.reload_mcp_deny()
    from cyt_mcp.catalog_build import refresh_catalog_cache

    await refresh_catalog_cache(
        context.server,
        context.cache,
        context.config_holder.config,
        skip_push=True,
    )
    new_hash = _catalog_hash(context.cache)
    _last_success_hash.pop(key, None)
    if context.list_changed_middleware is not None and new_hash != old_hash:
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
                    old_hash = _catalog_hash(context.cache)
                    context.config_holder.reload_mcp_deny()
                    _last_success_hash.pop(key, None)
                    new_hash = _catalog_hash(context.cache)
                    if new_hash != old_hash and context.list_changed_middleware is not None:
                        logger.info(
                            "cyt-mcp: permissions changed (sync push); restart session for list_changed",
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
