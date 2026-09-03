"""HTTP handler for ``POST /hook/connect`` on the colocated CYT server."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response

from cyt.config import (
    inject_via_for_agent,
    load_config,
    verify_only_mode,
)
from cyt.pruners.remote import PrunerSettingsCache
from cyt.skills.cli import HookRunResult, run_hook_payload
from cyt.skills.hook_payload import normalize_hook_payload
from cyt.skills.hook_quiet import configure_hook_quiet

logger = logging.getLogger(__name__)

HOOK_DEBUG_HEADER = "X-CYT-Hook-Debug"


def _hook_debug_enabled(request: Request) -> bool:
    return request.headers.get(HOOK_DEBUG_HEADER, "").strip() == "1"


def _session_log_path_from_payload(payload: dict[str, Any], agent: str) -> Path | None:
    from cyt_client.sessions import session_log_path

    enriched = dict(payload)
    enriched.setdefault("cyt_agent", agent)
    return session_log_path(enriched)


async def _run_verify_session_log(
    payload: dict[str, Any],
    config: dict[str, Any],
    *,
    agent: str,
) -> list[dict[str, Any]]:
    from cyt.cyt_mcp.catalog import get_cyt_mcp_catalog
    from cyt.injection.verify_session_log import append_verify_session_log

    tools = get_cyt_mcp_catalog(config, blocking=True) or []
    log_path = _session_log_path_from_payload(payload, agent)
    if log_path is None:
        return []
    inject_path = inject_via_for_agent(config, agent)
    return append_verify_session_log(
        log_path,
        tools,
        agent=agent,
        tools_inject_enabled=False,
        hallucination_gate_enabled=True,
        inject_via=inject_path,
    )


def _hook_connect_system_exit_response(exc: SystemExit) -> JSONResponse:
    message = str(exc).strip() or "hook credentials missing"
    if message.isdigit():
        message = "hook pruning pipeline aborted (missing API key or credential)"
    logger.error("hook connect aborted: %s", message)
    return JSONResponse({"error": message}, status_code=500)


async def _read_hook_connect_payload(
    request: Request,
) -> tuple[dict[str, Any], dict[str, Any]] | Response:
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
    return payload, payload_raw


async def _hook_connect_verify_only(
    payload: dict[str, Any],
    config: dict[str, Any],
    *,
    agent: str,
) -> Response:
    try:
        session_log = await _run_verify_session_log(payload, config, agent=agent)
    except Exception as exc:
        logger.exception("verify-only session log failed")
        return JSONResponse({"error": str(exc)}, status_code=500)
    result = HookRunResult(
        stdout_text="",
        outcome="verify_only",
        details={"session_log": session_log},
        session_log=session_log,
        cyt_agent=agent,
    )
    return PlainTextResponse(
        _format_verify_connect_response(result, session_log=session_log),
        status_code=200,
    )


def _format_verify_connect_response(
    result: HookRunResult,
    *,
    session_log: list[dict[str, Any]] | None,
) -> str:
    output: dict[str, Any] = {
        "verify-only": True,
        "hookSpecificOutput": {},
    }
    if result.cyt_agent:
        output["cytAgent"] = result.cyt_agent
    log_entries = session_log if session_log is not None else result.session_log
    if log_entries:
        output["cytSessionLog"] = log_entries
    return json.dumps(output, separators=(",", ":"))


async def hook_connect(request: Request) -> Response:
    """Run hook injection or verify-only connect for JSON body."""
    configure_hook_quiet()
    parsed = await _read_hook_connect_payload(request)
    if isinstance(parsed, Response):
        return parsed
    payload, payload_raw = parsed

    config: dict[str, Any] = getattr(request.app.state, "cyt_config", None) or load_config()
    pruner_settings: PrunerSettingsCache | None = getattr(
        request.app.state,
        "pruner_settings",
        None,
    )
    debug = _hook_debug_enabled(request)

    from cyt.hook.workspace_config import resolve_hook_request_config, with_dynamic_catalog_url
    from cyt.skills.cli import resolve_effective_hook_agent

    agent = resolve_effective_hook_agent(payload) or "cursor"
    spawn_config = getattr(request.app.state, "cyt_config", None)
    config, _workspace = resolve_hook_request_config(
        payload,
        agent,
        base_config=spawn_config or config,
    )
    config = with_dynamic_catalog_url(config, payload)

    if verify_only_mode(config) and inject_via_for_agent(config, agent) == "hook":
        return await _hook_connect_verify_only(payload, config, agent=agent)

    try:
        result = await _run_hook_in_thread(
            payload,
            config,
            request_payload=payload_raw,
            debug=debug,
            pruner_settings=pruner_settings,
        )
    except SystemExit as exc:
        return _hook_connect_system_exit_response(exc)
    except Exception as exc:
        logger.exception("hook connect failed")
        return JSONResponse({"error": str(exc)}, status_code=500)

    if not result.stdout_text:
        return PlainTextResponse("", status_code=200)
    return PlainTextResponse(result.stdout_text, status_code=200)


# Backward-compatible alias for existing imports/tests during transition.
hook_inject = hook_connect


async def _run_hook_in_thread(
    payload: dict[str, Any],
    config: dict[str, Any],
    *,
    request_payload: dict[str, Any] | None = None,
    debug: bool = False,
    pruner_settings: PrunerSettingsCache | None = None,
) -> HookRunResult:
    import asyncio

    return await asyncio.to_thread(
        run_hook_payload,
        payload,
        config,
        request_payload=request_payload,
        plain_output=False,
        debug=debug,
        io_guarded=True,
        allow_transcript_file_read=False,
        pruner_settings=pruner_settings,
    )
