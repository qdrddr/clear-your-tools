"""HTTP transport for cyt-client (stdlib only)."""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

POST_TIMEOUT_SECONDS = 55.0  # must stay below agent hook timeout (see USER_PROMPT_TIMEOUT_SECONDS)


def post_hook_inject(url: str, payload_bytes: bytes) -> tuple[int, bytes]:
    request = Request(
        url,
        data=payload_bytes,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=POST_TIMEOUT_SECONDS) as response:
            code = response.getcode()
            body = response.read()
            return (int(code) if isinstance(code, int) else 200, body)
    except HTTPError as exc:
        body = exc.read()
        return exc.code, body
    except URLError as exc:
        raise ConnectionError(str(exc.reason)) from exc


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
