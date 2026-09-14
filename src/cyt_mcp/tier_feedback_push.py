"""Push cyt-mcp tool-use tier feedback to the hook daemon (stdlib-only, non-blocking)."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from cyt.hook.daemon_client import resolve_hook_path
from cyt_mcp.catalog import catalog_tools_content_hash
from cyt_mcp.config import AggregatorConfig
from cyt_mcp.config_holder import ConfigHolder
from cyt_mcp.runtime_cache import RuntimeToolCache

logger = logging.getLogger(__name__)

TIER_FEEDBACK_PATH = "/hook/tier/feedback"
PUSH_TIMEOUT_SECONDS = 0.5

_report_tasks: set[asyncio.Task[None]] = set()


def _optional_properties_used(args: dict[str, Any] | None) -> bool:
    if not isinstance(args, dict) or not args:
        return False
    if len(args) > 1:
        return True
    for value in args.values():
        if value not in (None, "", [], {}):
            return True
    return False


def _post_json(url: str, payload: dict[str, Any]) -> bool:
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
            return isinstance(code, int) and 200 <= code < 300
    except (HTTPError, URLError, OSError, TimeoutError, ValueError) as exc:
        logger.debug("cyt-mcp tier feedback push failed: %s", exc)
        return False


def _build_payload(
    *,
    config: AggregatorConfig,
    tool_name: str,
    args: dict[str, Any] | None,
    success: bool,
    catalog_content_hash: str | None,
    mcp_server: str | None,
    bare_tool_name: str | None,
    input_schema: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if config.workspace_root is None:
        return None
    try:
        workspace = str(config.workspace_root.expanduser().resolve())
    except OSError:
        return None
    body: dict[str, Any] = {
        "event": "tool_used",
        "source": "cyt_mcp",
        "success": success,
        "workspace_root": workspace,
        "tool_name": tool_name,
        "catalog": "cyt_mcp",
        "args": args if isinstance(args, dict) else {},
        "optional_used": _optional_properties_used(args),
    }
    if catalog_content_hash:
        body["catalog_content_hash"] = catalog_content_hash
    if success and mcp_server and bare_tool_name and isinstance(input_schema, dict):
        body["mcp_server"] = mcp_server
        body["bare_tool_name"] = bare_tool_name
        body["input_schema"] = input_schema
    return body


def _push_sync(payload: dict[str, Any]) -> None:
    url = resolve_hook_path(TIER_FEEDBACK_PATH)
    if url is None:
        return
    _post_json(url, payload)


async def _maybe_sync_catalog(
    *,
    server: Any,
    cache: RuntimeToolCache,
    config_holder: ConfigHolder,
    catalog_content_hash: str | None,
) -> None:
    from cyt_mcp.catalog_build import refresh_catalog_cache
    from cyt_mcp.hook_daemon_push import _last_success_hash, _instance_key, schedule_catalog_push

    config = config_holder.config
    current_hash = catalog_tools_content_hash(cache.snapshot())
    if catalog_content_hash and catalog_content_hash == current_hash:
        return
    try:
        await refresh_catalog_cache(server, cache, config, skip_push=True)
    except Exception as exc:
        logger.debug("cyt-mcp catalog refresh before tier feedback failed: %s", exc)
        return
    new_hash = catalog_tools_content_hash(cache.snapshot())
    key = _instance_key(config)
    last = _last_success_hash.get(key)
    if last != new_hash:
        schedule_catalog_push(cache, config)


async def _report_async(
    *,
    config: AggregatorConfig,
    tool_name: str,
    args: dict[str, Any] | None,
    success: bool,
    catalog_content_hash: str | None,
    mcp_server: str | None,
    bare_tool_name: str | None,
    input_schema: dict[str, Any] | None,
    server: Any | None,
    cache: RuntimeToolCache | None,
    config_holder: ConfigHolder | None,
) -> None:
    payload = _build_payload(
        config=config,
        tool_name=tool_name,
        args=args,
        success=success,
        catalog_content_hash=catalog_content_hash,
        mcp_server=mcp_server,
        bare_tool_name=bare_tool_name,
        input_schema=input_schema,
    )
    if payload is None:
        return
    if server is not None and cache is not None and config_holder is not None:
        await _maybe_sync_catalog(
            server=server,
            cache=cache,
            config_holder=config_holder,
            catalog_content_hash=catalog_content_hash,
        )
    await asyncio.to_thread(_push_sync, payload)


def schedule_tool_use_feedback(
    *,
    config: AggregatorConfig,
    tool_name: str,
    args: dict[str, Any] | None,
    success: bool,
    catalog_content_hash: str | None = None,
    mcp_server: str | None = None,
    bare_tool_name: str | None = None,
    input_schema: dict[str, Any] | None = None,
    server: Any | None = None,
    cache: RuntimeToolCache | None = None,
    config_holder: ConfigHolder | None = None,
) -> None:
    """Fire-and-forget tier feedback report to hook daemon."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        payload = _build_payload(
            config=config,
            tool_name=tool_name,
            args=args,
            success=success,
            catalog_content_hash=catalog_content_hash,
            mcp_server=mcp_server,
            bare_tool_name=bare_tool_name,
            input_schema=input_schema,
        )
        if payload is None:
            return
        thread = threading.Thread(
            target=_push_sync,
            args=(payload,),
            name="cyt-mcp-tier-feedback",
            daemon=True,
        )
        thread.start()
        return

    task = loop.create_task(
        _report_async(
            config=config,
            tool_name=tool_name,
            args=args,
            success=success,
            catalog_content_hash=catalog_content_hash,
            mcp_server=mcp_server,
            bare_tool_name=bare_tool_name,
            input_schema=input_schema,
            server=server,
            cache=cache,
            config_holder=config_holder,
        ),
        name="cyt-mcp-tier-feedback",
    )
    _report_tasks.add(task)
    task.add_done_callback(_report_tasks.discard)
