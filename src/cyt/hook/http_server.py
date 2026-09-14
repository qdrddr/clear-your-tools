"""HTTP handler for ``POST /hook/connect`` on the colocated CYT server."""

from __future__ import annotations

import json
import logging
import os
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


def _is_localhost_request(request: Request) -> bool:
    client = request.client
    if client is None:
        return False
    host = str(client.host or "").strip()
    return host in {"127.0.0.1", "::1", "localhost"}


async def hook_catalog_register(request: Request) -> Response:
    if not _is_localhost_request(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    try:
        body = await request.body()
        payload = json.loads(body)
        if not isinstance(payload, dict):
            return JSONResponse({"error": "payload must be a JSON object"}, status_code=400)
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    from cyt.hook.catalog_registry import RegisterStatus, register_catalog

    result = register_catalog(payload)
    if result.status == RegisterStatus.INVALID:
        return JSONResponse({"error": result.message or "invalid request"}, status_code=400)
    if result.status == RegisterStatus.UNKNOWN_HASH:
        return JSONResponse({"error": result.message or "unknown hash"}, status_code=404)
    if result.status == RegisterStatus.UNCHANGED:
        # Use 200 so uvicorn delivers the JSON body. HTTP 204 No Content drops the
        # payload on the wire; cyt-mcp hash-only heartbeats then miss
        # permissions_revision and never reload MCP deny overlays.
        return JSONResponse(
            {"status": "unchanged", "permissions_revision": result.permissions_revision},
            status_code=200,
        )
    return JSONResponse(
        {"status": "stored", "permissions_revision": result.permissions_revision},
        status_code=200,
    )


async def hook_catalog_deregister(request: Request) -> Response:
    if not _is_localhost_request(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    try:
        body = await request.body()
        payload = json.loads(body)
        if not isinstance(payload, dict):
            return JSONResponse({"error": "payload must be a JSON object"}, status_code=400)
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    from cyt.hook.catalog_registry import deregister_catalog

    removed = deregister_catalog(payload)
    return PlainTextResponse("", status_code=200 if removed else 404)


async def hook_catalog_status(request: Request) -> Response:
    if not _is_localhost_request(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    from cyt.hook.catalog_registry import list_catalog_registrations

    return JSONResponse({"registrations": list_catalog_registrations()})


async def hook_tool_examples_record(request: Request) -> Response:
    if not _is_localhost_request(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    try:
        body = await request.body()
        payload = json.loads(body)
        if not isinstance(payload, dict):
            return JSONResponse({"error": "payload must be a JSON object"}, status_code=400)
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    workspace_raw = payload.get("workspace_root")
    workspace: Path | None = None
    if isinstance(workspace_raw, str) and workspace_raw.strip():
        workspace = Path(os.path.expanduser(workspace_raw.strip()))  # noqa: ASYNC240

    config: dict[str, Any] = getattr(request.app.state, "cyt_config", None) or load_config()
    from cyt.hook.workspace_config import resolve_hook_request_config, set_hook_workspace_in_config

    if workspace is not None:
        config = set_hook_workspace_in_config(config, workspace)
    else:
        agent = str(payload.get("agent") or "cursor")
        config, workspace = resolve_hook_request_config(payload, agent, base_config=config)
        config = set_hook_workspace_in_config(config, workspace)

    from cyt.tiers.config import resolve_tier_project
    from cyt.tool_examples.config import examples_active
    from cyt.tool_examples.record import record_tool_examples_capture

    if not examples_active(config) or resolve_tier_project(workspace=workspace) is None:
        return PlainTextResponse("", status_code=204)

    mcp_server = payload.get("mcp_server")
    tool_name = payload.get("tool_name")
    input_schema = payload.get("input_schema")
    args = payload.get("args")
    if (
        not isinstance(mcp_server, str)
        or not mcp_server.strip()
        or not isinstance(tool_name, str)
        or not tool_name.strip()
        or not isinstance(input_schema, dict)
        or not isinstance(args, dict)
    ):
        return JSONResponse(
            {"error": "mcp_server, tool_name, input_schema, args required"},
            status_code=400,
        )

    from cyt.tool_examples.identity import is_valid_tool_example_identity
    from cyt_mcp.config import load_known_mcp_server_keys
    from cyt_mcp.tool_identity import canonical_backend_identity, wire_name_for

    resolved_server = mcp_server.strip()
    resolved_tool = tool_name.strip()
    server_keys = load_known_mcp_server_keys()
    if server_keys:
        resolved_server, resolved_tool = canonical_backend_identity(
            {
                "name": wire_name_for(resolved_server, resolved_tool),
                "server_key": resolved_server,
                "tool_name": resolved_tool,
            },
            server_keys,
        )

    if not is_valid_tool_example_identity(resolved_server, resolved_tool):
        return PlainTextResponse("", status_code=204)

    record_tool_examples_capture(
        workspace=workspace,
        mcp_server=resolved_server,
        tool_name=resolved_tool,
        input_schema=input_schema,
        args=args,
        config=config,
    )
    return PlainTextResponse("", status_code=204)


def _resolve_tier_feedback_context(
    payload: dict[str, Any],
    base_config: dict[str, Any],
) -> tuple[dict[str, Any], Path | None]:
    workspace_raw = payload.get("workspace_root")
    workspace: Path | None = None
    if isinstance(workspace_raw, str) and workspace_raw.strip():
        workspace = Path(os.path.expanduser(workspace_raw.strip()))

    from cyt.hook.workspace_config import resolve_hook_request_config, set_hook_workspace_in_config

    config = base_config
    if workspace is not None:
        config = set_hook_workspace_in_config(config, workspace)
    else:
        agent = str(payload.get("agent") or "cursor")
        config, workspace = resolve_hook_request_config(payload, agent, base_config=config)
        config = set_hook_workspace_in_config(config, workspace)
    return config, workspace


def _tier_feedback_tool_used(
    payload: dict[str, Any],
    *,
    config: dict[str, Any],
    workspace: Path | None,
) -> Response:
    from cyt.tiers.feedback import record_tool_attempt_feedback

    tool_name = payload.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name.strip():
        return JSONResponse({"error": "tool_name required"}, status_code=400)
    catalog = payload.get("catalog")
    catalog_str = catalog if isinstance(catalog, str) else None
    args = payload.get("args")
    args_dict = args if isinstance(args, dict) else None
    optional_used = payload.get("optional_used") is True
    success_raw = payload.get("success")
    success = success_raw is not False
    record_tool_attempt_feedback(
        tool_name=tool_name.strip(),
        catalog=catalog_str,
        success=success,
        config=config,
        args=args_dict,
        workspace=workspace,
        optional_used=optional_used,
    )
    if success and isinstance(args_dict, dict):
        mcp_server = payload.get("mcp_server")
        bare_tool = payload.get("bare_tool_name")
        input_schema = payload.get("input_schema")
        from cyt.tool_examples.identity import is_valid_tool_example_identity
        from cyt_mcp.config import load_known_mcp_server_keys
        from cyt_mcp.tool_identity import canonical_backend_identity, wire_name_for

        resolved_server = mcp_server.strip() if isinstance(mcp_server, str) else ""
        resolved_tool = bare_tool.strip() if isinstance(bare_tool, str) else ""
        server_keys = load_known_mcp_server_keys()
        if server_keys and resolved_server and resolved_tool:
            resolved_server, resolved_tool = canonical_backend_identity(
                {
                    "name": wire_name_for(resolved_server, resolved_tool),
                    "server_key": resolved_server,
                    "tool_name": resolved_tool,
                },
                server_keys,
            )

        if (
            isinstance(input_schema, dict)
            and is_valid_tool_example_identity(resolved_server, resolved_tool)
        ):
            from cyt.tool_examples.record import record_tool_examples_capture

            record_tool_examples_capture(
                workspace=workspace,
                mcp_server=resolved_server,
                tool_name=resolved_tool,
                input_schema=input_schema,
                args=args_dict,
                config=config,
            )
    return PlainTextResponse("", status_code=204)


def _tier_feedback_skill_used(
    payload: dict[str, Any],
    *,
    config: dict[str, Any],
    workspace: Path | None,
) -> Response:
    from cyt.tiers.feedback import record_skill_used_feedback

    entity_id = payload.get("entity_id")
    if not isinstance(entity_id, str) or not entity_id.strip():
        return JSONResponse({"error": "entity_id required"}, status_code=400)
    record_skill_used_feedback(
        entity_id.strip(),
        config=config,
        workspace=workspace,
    )
    return PlainTextResponse("", status_code=204)


async def hook_tier_feedback(request: Request) -> Response:
    if not _is_localhost_request(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    try:
        body = await request.body()
        payload = json.loads(body)
        if not isinstance(payload, dict):
            return JSONResponse({"error": "payload must be a JSON object"}, status_code=400)
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    event = payload.get("event")
    if event not in {"tool_used", "skill_used"}:
        return JSONResponse({"error": "unsupported event"}, status_code=400)

    config: dict[str, Any] = getattr(request.app.state, "cyt_config", None) or load_config()
    config, workspace = _resolve_tier_feedback_context(payload, config)

    from cyt.tiers.config import resolve_tier_project

    if resolve_tier_project(workspace=workspace) is None:
        return PlainTextResponse("", status_code=204)

    if event == "tool_used":
        return _tier_feedback_tool_used(payload, config=config, workspace=workspace)
    return _tier_feedback_skill_used(payload, config=config, workspace=workspace)


async def hook_permissions_changed(request: Request) -> Response:
    if not _is_localhost_request(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    try:
        body = await request.body()
        payload = json.loads(body)
        if not isinstance(payload, dict):
            return JSONResponse({"error": "payload must be a JSON object"}, status_code=400)
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    from cyt.hook.catalog_registry import normalize_registry_workspace_path
    from cyt.hook.permissions_react import react_to_permissions_changed
    from cyt.hook.permissions_revision import bump_permissions_revision

    workspace_root = normalize_registry_workspace_path(payload.get("workspace_root"))
    if workspace_root is None:
        return JSONResponse({"error": "invalid workspace_root"}, status_code=400)
    agent = str(payload.get("agent") or "cursor").strip().lower() or "cursor"
    revision = bump_permissions_revision(agent, workspace_root)
    react_to_permissions_changed(agent=agent, workspace_root=workspace_root)
    return JSONResponse({"status": "ok", "permissions_revision": revision}, status_code=200)


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

    from cyt.hook.workspace_config import resolve_hook_request_config, set_hook_workspace_in_config
    from cyt.skills.cli import resolve_effective_hook_agent

    agent = resolve_effective_hook_agent(payload) or "cursor"
    spawn_config = getattr(request.app.state, "cyt_config", None)
    config, workspace = resolve_hook_request_config(
        payload,
        agent,
        base_config=spawn_config or config,
    )
    config = set_hook_workspace_in_config(config, workspace)

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
