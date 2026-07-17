"""Load tool catalogs from the local ``mcpc`` CLI."""

from __future__ import annotations

import copy
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, cast

from cyt.mcpc.catalog_disk import (
    normalize_mcpc_executable_slug,
    raw_catalog_content_hash,
    read_disk_catalog,
    write_disk_catalog,
)
from cyt.mcpc.cli import mcpc_available, run_mcpc_json
from cyt.mcpc.runtime import (
    load_config,
    tools_hook_mcpc_executable,
    uses_mcpc_tool_catalog,
)
from cyt.mcpc.session_health import (
    clear_session_health_cache,
    debug_disk_enabled,
    eligible_session_names,
    filter_catalog_by_session_health,
    health_snapshot_to_disk,
    load_session_health_from_disk,
    refresh_session_health,
    session_health_snapshot_fields,
    snapshot_health_for_catalog,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _McpcCacheKey:
    executable: str
    slug: str


@dataclass
class _McpcCatalogState:
    tools: list[dict[str, Any]] = field(default_factory=list)
    sessions: dict[str, dict[str, Any]] = field(default_factory=dict)
    updated_at: float = 0.0
    catalog_content_hash: str = ""


_catalog_lock = threading.Lock()
_catalog_states: dict[_McpcCacheKey, _McpcCatalogState] = {}


def clear_mcpc_catalog_cache() -> None:
    with _catalog_lock:
        _catalog_states.clear()
    clear_session_health_cache()
    from cyt.mcpc.cache_scheduler import clear_mcpc_cache_schedulers

    clear_mcpc_cache_schedulers()


def _mcpc_runtime_active(config: dict[str, Any]) -> bool:
    return uses_mcpc_tool_catalog(config)


def _cache_key_for_config(config: dict[str, Any]) -> _McpcCacheKey:
    executable = tools_hook_mcpc_executable(config)
    slug = normalize_mcpc_executable_slug(executable)
    return _McpcCacheKey(executable=executable, slug=slug)


def _get_state(cache_key: _McpcCacheKey) -> _McpcCatalogState:
    with _catalog_lock:
        state = _catalog_states.get(cache_key)
        if state is None:
            state = _McpcCatalogState()
            _catalog_states[cache_key] = state
        return state


def _snapshot_tools(state: _McpcCatalogState) -> list[dict[str, Any]]:
    with _catalog_lock:
        return copy.deepcopy(state.tools)


def _apply_catalog_to_state(
    state: _McpcCatalogState,
    tools: list[dict[str, Any]],
    *,
    content_hash: str,
    sessions: dict[str, dict[str, Any]] | None = None,
) -> None:
    with _catalog_lock:
        state.tools = tools
        state.updated_at = time.monotonic()
        state.catalog_content_hash = content_hash
        if sessions is not None:
            state.sessions = copy.deepcopy(sessions)


def _normalize_tool(
    *,
    session_name: str,
    tool: dict[str, Any],
    server_name: str,
    server_instructions: str,
) -> dict[str, Any] | None:
    tool_name = str(tool.get("name") or "").strip()
    if not tool_name:
        return None
    schema = tool.get("inputSchema")
    if not isinstance(schema, dict):
        schema = tool.get("input_schema")
    if not isinstance(schema, dict):
        schema = {}
    description = str(tool.get("description") or "").strip()
    title = str(tool.get("title") or tool_name).strip()
    return {
        "name": f"{session_name}/{tool_name}",
        "tool_name": tool_name,
        "mcpc_session": session_name,
        "title": title,
        "description": description,
        "input_schema": schema,
        "server_name": server_name,
        "server_instructions": server_instructions,
    }


def _session_server_name(session_info: dict[str, Any]) -> str:
    server_info = session_info.get("serverInfo")
    if isinstance(server_info, dict):
        name = str(server_info.get("name") or "").strip()
        if name:
            return name
    return ""


def _session_instructions(session_info: dict[str, Any]) -> str:
    instructions = session_info.get("instructions")
    if isinstance(instructions, str):
        return instructions.strip()
    return ""


def _fetch_session_tools(
    executable: str,
    session_name: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    tools_payload = run_mcpc_json(executable, [session_name, "tools-list"])
    info_payload = run_mcpc_json(executable, [session_name])
    tools_raw: list[dict[str, Any]] = []
    if isinstance(tools_payload, list):
        tools_raw = [cast(dict[str, Any], item) for item in tools_payload if isinstance(item, dict)]
    session_info: dict[str, Any] = (
        cast(dict[str, Any], info_payload) if isinstance(info_payload, dict) else {}
    )
    return tools_raw, session_info


def _fetch_catalog_from_cli(
    executable: str,
    slug: str,
    *,
    config: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    cfg = config or load_config()
    refresh_session_health(executable=executable, slug=slug, config=cfg)
    eligible = eligible_session_names(slug, config=cfg)

    sessions_payload = run_mcpc_json(executable, [])
    sessions_meta: dict[str, dict[str, Any]] = {}
    if isinstance(sessions_payload, dict):
        payload_dict = cast(dict[str, Any], sessions_payload)
        raw_sessions = payload_dict.get("sessions")
        if isinstance(raw_sessions, list):
            for item in raw_sessions:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                if name:
                    sessions_meta[name] = item

    tools: list[dict[str, Any]] = []
    session_details: dict[str, dict[str, Any]] = {}
    for session_name in sorted(eligible):
        tools_raw, session_info = _fetch_session_tools(executable, session_name)
        server_name = _session_server_name(session_info)
        server_instructions = _session_instructions(session_info)
        session_details[session_name] = {
            "server_name": server_name,
            "server_instructions": server_instructions,
            "status": str(sessions_meta.get(session_name, {}).get("status") or "live"),
        }
        for tool in tools_raw:
            normalized = _normalize_tool(
                session_name=session_name,
                tool=tool,
                server_name=server_name,
                server_instructions=server_instructions,
            )
            if normalized is not None:
                tools.append(normalized)
    return tools, session_details


def _write_catalog_disk(
    cache_key: _McpcCacheKey,
    *,
    tools: list[dict[str, Any]],
    sessions: dict[str, dict[str, Any]] | None = None,
    config: dict[str, Any] | None = None,
) -> None:
    cfg = config or load_config()
    sessions_health = None
    if debug_disk_enabled():
        snapshot = snapshot_health_for_catalog(cache_key.slug)
        if snapshot is not None:
            sessions_health = health_snapshot_to_disk(snapshot, slug=cache_key.slug, config=cfg)
    write_disk_catalog(
        cache_key.slug,
        mcpc_executable=cache_key.executable,
        tools=tools,
        content_hash=raw_catalog_content_hash(tools),
        sessions=sessions,
        sessions_health=sessions_health,
    )


def _load_catalog_from_disk(
    cache_key: _McpcCacheKey,
    *,
    config: dict[str, Any] | None = None,
) -> bool:
    envelope = read_disk_catalog(cache_key.slug)
    if envelope is None:
        return False
    tools = envelope.get("tools")
    if not isinstance(tools, list):
        return False
    content_hash = str(envelope.get("catalog_content_hash") or raw_catalog_content_hash(tools))
    raw_sessions = envelope.get("sessions")
    sessions = raw_sessions if isinstance(raw_sessions, dict) else {}
    state = _get_state(cache_key)
    _apply_catalog_to_state(
        state,
        copy.deepcopy(tools),
        content_hash=content_hash,
        sessions=sessions,
    )
    load_session_health_from_disk(
        cache_key.slug,
        envelope.get("sessions_health")
        if isinstance(envelope.get("sessions_health"), dict)
        else None,
        config=config,
    )
    logger.info(
        "mcpc catalog disk_hit slug=%s catalog_content_hash=%s tool_count=%d",
        cache_key.slug,
        content_hash[:12],
        len(tools),
    )
    return True


def load_mcpc_catalog_from_disk(config: dict[str, Any] | None = None) -> bool:
    cfg = config or load_config()
    if not _mcpc_runtime_active(cfg):
        return False
    cache_key = _cache_key_for_config(cfg)
    state = _get_state(cache_key)
    with _catalog_lock:
        if state.tools:
            return True
    return _load_catalog_from_disk(cache_key, config=cfg)


def _ensure_scheduler_started(cfg: dict[str, Any]) -> None:
    from cyt.mcpc.cache_scheduler import start_mcpc_cache_scheduler

    start_mcpc_cache_scheduler(cfg)


def _blocking_cli_fetch(
    cfg: dict[str, Any],
    cache_key: _McpcCacheKey,
    *,
    apply_health_filter: bool = True,
) -> list[dict[str, Any]]:
    state = _get_state(cache_key)
    if not mcpc_available(cache_key.executable):
        logger.warning("mcpc executable unavailable: %s", cache_key.executable)
        stale = _snapshot_tools(state)
        return _return_catalog(
            stale,
            cache_key,
            apply_health_filter=apply_health_filter,
            config=cfg,
        )

    try:
        logger.info("mcpc catalog cli_fetch slug=%s blocking=true", cache_key.slug)
        tools, sessions = _fetch_catalog_from_cli(
            cache_key.executable,
            cache_key.slug,
            config=cfg,
        )
    except Exception as exc:
        logger.warning("mcpc catalog fetch failed: %s", exc)
        stale = _snapshot_tools(state)
        return _return_catalog(
            stale,
            cache_key,
            apply_health_filter=apply_health_filter,
            config=cfg,
        )

    content_hash = raw_catalog_content_hash(tools)
    _apply_catalog_to_state(state, tools, content_hash=content_hash, sessions=sessions)
    _write_catalog_disk(cache_key, tools=tools, sessions=sessions, config=cfg)
    _ensure_scheduler_started(cfg)
    return _return_catalog(
        copy.deepcopy(tools),
        cache_key,
        apply_health_filter=apply_health_filter,
        config=cfg,
    )


def _return_catalog(
    tools: list[dict[str, Any]],
    cache_key: _McpcCacheKey,
    *,
    apply_health_filter: bool,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not tools:
        return []
    if not apply_health_filter:
        return tools
    return filter_catalog_by_session_health(tools, cache_key.slug, config=config)


def _get_mcpc_catalog_impl(
    cfg: dict[str, Any],
    *,
    blocking: bool,
    force: bool,
    apply_health_filter: bool = True,
) -> list[dict[str, Any]] | None:
    cache_key = _cache_key_for_config(cfg)
    state = _get_state(cache_key)

    with _catalog_lock:
        has_memory = bool(state.tools)

    if not has_memory:
        _load_catalog_from_disk(cache_key, config=cfg)
        with _catalog_lock:
            has_memory = bool(state.tools)

    if not has_memory:
        if blocking or force:
            return _blocking_cli_fetch(cfg, cache_key, apply_health_filter=apply_health_filter)
        _ensure_scheduler_started(cfg)
        return _return_catalog([], cache_key, apply_health_filter=apply_health_filter, config=cfg)

    if force and blocking:
        return _blocking_cli_fetch(cfg, cache_key, apply_health_filter=apply_health_filter)

    logger.debug(
        "mcpc catalog cache_hit slug=%s tool_count=%d",
        cache_key.slug,
        len(state.tools),
    )
    _ensure_scheduler_started(cfg)
    return _return_catalog(
        _snapshot_tools(state),
        cache_key,
        apply_health_filter=apply_health_filter,
        config=cfg,
    )


def get_mcpc_catalog(
    config: dict[str, Any] | None = None,
    *,
    blocking: bool = False,
    force: bool = False,
) -> list[dict[str, Any]] | None:
    """Unified SWR entrypoint: memory snapshot only on hook path (never blocks on refresh)."""
    cfg = config or load_config()
    if not _mcpc_runtime_active(cfg):
        return None
    return _get_mcpc_catalog_impl(cfg, blocking=blocking, force=force)


def load_mcpc_tools(
    config: dict[str, Any] | None = None,
    *,
    blocking: bool = False,
) -> list[dict[str, Any]] | None:
    return get_mcpc_catalog(config, blocking=blocking, force=False)


def mcpc_catalog_health_snapshot(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config or load_config()
    if not _mcpc_runtime_active(cfg):
        return {}
    cache_key = _cache_key_for_config(cfg)
    state = _get_state(cache_key)
    with _catalog_lock:
        tool_count = len(state.tools)
        age_seconds = time.monotonic() - state.updated_at if state.updated_at else None
        content_hash = state.catalog_content_hash
    payload: dict[str, Any] = {"catalog_tool_count": tool_count}
    payload.update(session_health_snapshot_fields(cache_key.slug, config=cfg))
    if age_seconds is not None:
        payload["catalog_age_seconds"] = round(age_seconds, 1)
    if content_hash:
        payload["catalog_content_hash_prefix"] = content_hash[:12]
    payload["mcpc_catalog_slug"] = cache_key.slug
    return payload


def apply_fetched_catalog(
    cache_key: _McpcCacheKey,
    tools: list[dict[str, Any]],
    sessions: dict[str, dict[str, Any]],
    *,
    config: dict[str, Any] | None = None,
) -> None:
    """Apply a freshly fetched catalog to memory (scheduler helper)."""
    state = _get_state(cache_key)
    content_hash = raw_catalog_content_hash(tools)
    _apply_catalog_to_state(state, tools, content_hash=content_hash, sessions=sessions)
