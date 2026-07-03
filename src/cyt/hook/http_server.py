"""HTTP handler for ``POST /hook/inject`` on the colocated CYT server."""

from __future__ import annotations

import json
import logging
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response

from cyt.config import load_config
from cyt.skills.cli import HookRunResult, run_hook_payload
from cyt.skills.hook_payload import normalize_hook_payload
from cyt.skills.hook_quiet import configure_hook_quiet

logger = logging.getLogger(__name__)


async def hook_inject(request: Request) -> Response:
    """Run hook injection for JSON body and return exact stdout bytes."""
    configure_hook_quiet()
    try:
        body = await request.body()
        if not body.strip():
            return PlainTextResponse("", status_code=200)
        payload_raw = json.loads(body)
        if not isinstance(payload_raw, dict):
            return JSONResponse({"error": "hook payload must be a JSON object"}, status_code=400)
        payload = normalize_hook_payload(payload_raw)
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    except (TypeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    config: dict[str, Any] = getattr(request.app.state, "cyt_config", None) or load_config()
    try:
        result = await _run_hook_in_thread(payload, config)
    except Exception as exc:
        logger.exception("hook inject failed")
        return JSONResponse({"error": str(exc)}, status_code=500)

    if not result.stdout_text:
        return PlainTextResponse("", status_code=200)
    return PlainTextResponse(result.stdout_text, status_code=200)


async def _run_hook_in_thread(payload: dict[str, Any], config: dict[str, Any]) -> HookRunResult:
    import asyncio

    return await asyncio.to_thread(
        run_hook_payload,
        payload,
        config,
        plain_output=False,
        debug=False,
        io_guarded=True,
        allow_transcript_file_read=False,
    )
