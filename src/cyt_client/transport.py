"""HTTP transport for cyt-client (stdlib only)."""

from __future__ import annotations

import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from cyt.common.agent_debug_log import agent_debug_log

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
    # #region agent log
    _post_start = time.perf_counter()
    agent_debug_log(
        "transport.py:post_hook_inject",
        "POST /hook/connect start",
        data={"url": url, "payload_bytes": len(payload_bytes), "timeout_s": timeout},
        hypothesis_id="A",
    )
    # #endregion
    try:
        with urlopen(request, timeout=timeout) as response:
            code = response.getcode()
            body = response.read()
            # #region agent log
            agent_debug_log(
                "transport.py:post_hook_inject",
                "POST /hook/connect ok",
                data={
                    "status": int(code) if isinstance(code, int) else 200,
                    "response_bytes": len(body),
                    "elapsed_ms": round((time.perf_counter() - _post_start) * 1000, 1),
                },
                hypothesis_id="A",
            )
            # #endregion
            return (int(code) if isinstance(code, int) else 200, body)
    except HTTPError as exc:
        body = exc.read()
        # #region agent log
        agent_debug_log(
            "transport.py:post_hook_inject",
            "POST /hook/connect HTTP error",
            data={
                "status": exc.code,
                "elapsed_ms": round((time.perf_counter() - _post_start) * 1000, 1),
            },
            hypothesis_id="A",
        )
        # #endregion
        return exc.code, body
    except TimeoutError as exc:
        # #region agent log
        agent_debug_log(
            "transport.py:post_hook_inject",
            "POST /hook/connect timeout",
            data={"timeout_s": timeout, "elapsed_ms": round((time.perf_counter() - _post_start) * 1000, 1)},
            hypothesis_id="A",
        )
        # #endregion
        raise ConnectionError(f"timed out after {timeout:g}s") from exc
    except URLError as exc:
        reason = exc.reason
        if isinstance(reason, TimeoutError):
            # #region agent log
            agent_debug_log(
                "transport.py:post_hook_inject",
                "POST /hook/connect URL timeout",
                data={"timeout_s": timeout, "elapsed_ms": round((time.perf_counter() - _post_start) * 1000, 1)},
                hypothesis_id="A",
            )
            # #endregion
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
