"""HTTP transport for cyt-client (stdlib only)."""

from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# Stay below agent hook timeout (see USER_PROMPT_TIMEOUT_SECONDS in hook setup).
DEFAULT_POST_TIMEOUT_SECONDS = 55.0
POST_TIMEOUT_ENV = "CYT_HOOK_POST_TIMEOUT_SECONDS"
HOOK_DEBUG_HEADER = "X-CYT-Hook-Debug"


def post_timeout_seconds() -> float:
    raw = os.environ.get(POST_TIMEOUT_ENV, "").strip()
    if raw:
        try:
            return max(1.0, float(raw))
        except ValueError:
            pass
    return DEFAULT_POST_TIMEOUT_SECONDS


def post_hook_inject(url: str, payload_bytes: bytes, *, debug: bool = False) -> tuple[int, bytes]:
    headers = {"Content-Type": "application/json"}
    if debug:
        headers[HOOK_DEBUG_HEADER] = "1"
    request = Request(
        url,
        data=payload_bytes,
        headers=headers,
        method="POST",
    )
    timeout = post_timeout_seconds()
    try:
        with urlopen(request, timeout=timeout) as response:
            code = response.getcode()
            body = response.read()
            return (int(code) if isinstance(code, int) else 200, body)
    except HTTPError as exc:
        body = exc.read()
        return exc.code, body
    except TimeoutError as exc:
        raise ConnectionError(f"timed out after {timeout:g}s") from exc
    except URLError as exc:
        reason = exc.reason
        if isinstance(reason, TimeoutError):
            raise ConnectionError(f"timed out after {timeout:g}s") from exc
        raise ConnectionError(str(reason)) from exc


def parse_error_body(body: bytes) -> str:
    if not body.strip():
        return "hook server error"
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return body.decode("utf-8", errors="replace")
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, str):
            return error
    return body.decode("utf-8", errors="replace")
