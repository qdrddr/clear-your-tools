"""Notify hook daemon of tier feedback events (stdlib only)."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from cyt_client.port import HOOK_CONNECT_PATH, HOOK_TIER_FEEDBACK_PATH, resolve_hook_url
from cyt_client.rules_file import workspace_root_from_payload
from cyt_client.transport import post_timeout_seconds


def _optional_properties_used(args: dict[str, Any] | None) -> bool:
    if not isinstance(args, dict) or not args:
        return False
    if len(args) > 1:
        return True
    for value in args.values():
        if value not in (None, "", [], {}):
            return True
    return False


_TIER_FEEDBACK_TIMEOUT_SECONDS = 0.5


def _tier_feedback_url() -> str | None:
    hook_url = resolve_hook_url()
    if hook_url is None:
        return None
    if hook_url.endswith(HOOK_CONNECT_PATH):
        return hook_url[: -len(HOOK_CONNECT_PATH)] + HOOK_TIER_FEEDBACK_PATH
    if hook_url.endswith("/"):
        return hook_url.rstrip("/") + HOOK_TIER_FEEDBACK_PATH
    return hook_url + HOOK_TIER_FEEDBACK_PATH


def notify_tool_used_feedback(
    payload: dict[str, Any],
    *,
    tool_name: str,
    catalog: str,
    args: dict[str, Any] | None = None,
) -> None:
    workspace = workspace_root_from_payload(payload)
    if workspace is None:
        return
    url = _tier_feedback_url()
    if url is None:
        return
    body = {
        "event": "tool_used",
        "workspace_root": str(workspace),
        "tool_name": tool_name,
        "catalog": catalog,
        "args": args if isinstance(args, dict) else {},
        "optional_used": _optional_properties_used(args),
    }
    request = Request(
        url,
        data=json.dumps(body, separators=(",", ":")).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    timeout = min(post_timeout_seconds(), _TIER_FEEDBACK_TIMEOUT_SECONDS)
    try:
        with urlopen(request, timeout=timeout) as response:
            response.read()
    except (HTTPError, URLError, OSError, TimeoutError):
        return


def notify_skill_used_feedback(
    payload: dict[str, Any],
    *,
    entity_id: str,
) -> None:
    workspace = workspace_root_from_payload(payload)
    if workspace is None or not entity_id.strip():
        return
    url = _tier_feedback_url()
    if url is None:
        return
    body = {
        "event": "skill_used",
        "workspace_root": str(workspace),
        "entity_id": entity_id.strip(),
    }
    request = Request(
        url,
        data=json.dumps(body, separators=(",", ":")).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    timeout = min(post_timeout_seconds(), _TIER_FEEDBACK_TIMEOUT_SECONDS)
    try:
        with urlopen(request, timeout=timeout) as response:
            response.read()
    except (HTTPError, URLError, OSError, TimeoutError):
        return
