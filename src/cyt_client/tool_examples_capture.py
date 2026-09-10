"""Notify hook daemon of successful tool example captures (stdlib only)."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from cyt_client.config import tool_examples_post_tool_capture_enabled
from cyt_client.port import HOOK_CONNECT_PATH, HOOK_TOOL_EXAMPLES_RECORD_PATH, resolve_hook_url
from cyt_client.rules_file import workspace_root_from_payload
from cyt_client.tool_gate import extract_post_tool_example_capture
from cyt_client.transport import post_timeout_seconds

_CAPTURE_TIMEOUT_SECONDS = 0.5


def _record_url() -> str | None:
    hook_url = resolve_hook_url()
    if hook_url is None:
        return None
    if hook_url.endswith(HOOK_CONNECT_PATH):
        return hook_url[: -len(HOOK_CONNECT_PATH)] + HOOK_TOOL_EXAMPLES_RECORD_PATH
    if hook_url.endswith("/"):
        return hook_url.rstrip("/") + HOOK_TOOL_EXAMPLES_RECORD_PATH
    return hook_url + HOOK_TOOL_EXAMPLES_RECORD_PATH


def notify_tool_examples_capture(payload: dict[str, Any]) -> None:
    if not tool_examples_post_tool_capture_enabled():
        return
    capture = extract_post_tool_example_capture(payload)
    if capture is None:
        return
    workspace = workspace_root_from_payload(payload)
    if workspace is None:
        return
    url = _record_url()
    if url is None:
        return
    body = {
        "workspace_root": str(workspace),
        "mcp_server": capture["mcp_server"],
        "tool_name": capture["tool_name"],
        "args": capture["args"],
        "input_schema": capture["input_schema"],
    }
    request = Request(
        url,
        data=json.dumps(body, separators=(",", ":")).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    timeout = min(post_timeout_seconds(), _CAPTURE_TIMEOUT_SECONDS)
    try:
        with urlopen(request, timeout=timeout) as response:
            response.read()
    except (HTTPError, URLError, OSError, TimeoutError):
        return
