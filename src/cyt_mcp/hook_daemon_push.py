"""Push cyt-mcp tool catalogs to the hook daemon (stdlib-only, non-blocking)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from cyt_mcp.catalog import catalog_payload
from cyt_mcp.config import AggregatorConfig
from cyt_mcp.runtime_cache import RuntimeToolCache

logger = logging.getLogger(__name__)

CatalogScope = Literal["global", "workspace"]

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
_instance_id = f"pid:{os.getpid()}"


def _instance_key(config: AggregatorConfig) -> str:
    scope = config.catalog_scope
    ws = str(config.workspace_root or "")
    return f"{config.agent}:{scope}:{ws}"


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
    payload_data = catalog_payload(cache, agent=config.agent)
    tools = payload_data.get("tools")
    if not isinstance(tools, list):
        tools = []
    from cyt_mcp.catalog import catalog_tools_content_hash

    content_hash = catalog_tools_content_hash(tools)
    body: dict[str, Any] = {
        "agent": config.agent,
        "scope": config.catalog_scope,
        "workspace_root": str(config.workspace_root) if config.workspace_root is not None else None,
        "instance_id": _instance_id,
        "content_hash": content_hash,
    }
    if include_tools:
        body["tools"] = tools
    return body


def _post_json(url: str, payload: dict[str, Any]) -> int:
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
            return int(code) if isinstance(code, int) else 200
    except HTTPError as exc:
        return int(exc.code)
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        logger.debug("cyt-mcp catalog push failed: %s", exc)
        return 0


def _push_once(config: AggregatorConfig, cache: RuntimeToolCache) -> bool:
    url = resolve_hook_register_url()
    if not url:
        return False

    key = _instance_key(config)
    last_hash = _last_success_hash.get(key)
    payload_data = catalog_payload(cache, agent=config.agent)
    tools = payload_data.get("tools")
    if not isinstance(tools, list):
        tools = []
    from cyt_mcp.catalog import catalog_tools_content_hash

    content_hash = catalog_tools_content_hash(tools)

    include_tools = last_hash != content_hash
    if not include_tools and last_hash == content_hash:
        body = _build_register_payload(config, cache, include_tools=False)
        status = _post_json(url, body)
        if status == 204:
            return True
        if status == 404:
            include_tools = True
        elif status == 200:
            return True
        elif status == 0:
            return False

    if include_tools or not last_hash:
        body = _build_register_payload(config, cache, include_tools=True)
        status = _post_json(url, body)
        if status in {200, 204}:
            _last_success_hash[key] = content_hash
            return True
        return False

    return False


async def _retry_push_loop(config: AggregatorConfig, cache: RuntimeToolCache) -> None:
    delay_index = 0
    while True:
        try:
            success = await asyncio.to_thread(_push_once, config, cache)
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


def schedule_catalog_push(cache: RuntimeToolCache, config: AggregatorConfig) -> None:
    """Fire-and-forget background push to hook daemon registry."""
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
            immediate_task = asyncio.create_task(
                _push_immediate(config, cache),
                name="cyt-mcp-catalog-push-immediate",
            )
            _immediate_push_tasks.add(immediate_task)
            immediate_task.add_done_callback(_immediate_push_tasks.discard)
            return
        task = loop.create_task(_retry_push_loop(config, cache), name=f"cyt-mcp-catalog-push-{key}")
        _push_tasks[key] = task


async def _push_immediate(config: AggregatorConfig, cache: RuntimeToolCache) -> None:
    await asyncio.to_thread(_push_once, config, cache)


def _push_sync_with_retry(config: AggregatorConfig, cache: RuntimeToolCache) -> None:
    delay_index = 0
    while True:
        if _push_once(config, cache):
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
        "scope": config.catalog_scope,
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
        task = _push_tasks.pop(key, None)
        if task is not None and not task.done():
            task.cancel()
