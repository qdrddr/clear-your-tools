"""In-memory definitions-file tool catalog with SWR reads."""

from __future__ import annotations

import copy
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cyt.config import (
    load_config,
    resolved_tools_hook_file,
    uses_definitions_tool_catalog,
)
from cyt.tools.sources.definitions import load_definitions_file

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _DefinitionsCacheKey:
    path: str


@dataclass
class _DefinitionsCatalogState:
    tools: list[dict[str, Any]] = field(default_factory=list)
    fingerprint: str = ""
    updated_at: float = 0.0


_catalog_lock = threading.Lock()
_catalog_states: dict[_DefinitionsCacheKey, _DefinitionsCatalogState] = {}


def clear_definitions_catalog_cache() -> None:
    with _catalog_lock:
        _catalog_states.clear()
    from cyt.tools.definitions_cache_scheduler import clear_definitions_cache_schedulers

    clear_definitions_cache_schedulers()


def _cache_key_for_config(config: dict[str, Any]) -> _DefinitionsCacheKey:
    path = str(resolved_tools_hook_file(config).expanduser())
    return _DefinitionsCacheKey(path=path)


def _get_state(cache_key: _DefinitionsCacheKey) -> _DefinitionsCatalogState:
    with _catalog_lock:
        state = _catalog_states.get(cache_key)
        if state is None:
            state = _DefinitionsCatalogState()
            _catalog_states[cache_key] = state
        return state


def _definitions_fingerprint(path: Path) -> str:
    try:
        resolved = path.expanduser()
        return f"{resolved}:{resolved.stat().st_mtime_ns}"
    except OSError:
        return f"{path}:missing"


def definitions_catalog_fingerprint(config: dict[str, Any] | None = None) -> str:
    cfg = config or load_config()
    if not uses_definitions_tool_catalog(cfg):
        return ""
    return _definitions_fingerprint(resolved_tools_hook_file(cfg))


def _snapshot_tools(state: _DefinitionsCatalogState) -> list[dict[str, Any]]:
    with _catalog_lock:
        return copy.deepcopy(state.tools)


def _apply_catalog_to_state(
    state: _DefinitionsCatalogState,
    tools: list[dict[str, Any]],
    *,
    fingerprint: str,
    config: dict[str, Any] | None = None,
) -> None:
    with _catalog_lock:
        state.tools = tools
        state.fingerprint = fingerprint
        state.updated_at = time.monotonic()
    if config is not None:
        from cyt.tools.master_cache_scheduler import schedule_master_catalog_refresh

        schedule_master_catalog_refresh(config)


def _load_catalog_from_disk(
    cache_key: _DefinitionsCacheKey,
    *,
    config: dict[str, Any] | None = None,
) -> bool:
    cfg = config or load_config()
    path = Path(cache_key.path)
    if not path.is_file():
        return False
    try:
        tools = load_definitions_file(path)
    except (OSError, ValueError) as exc:
        logger.warning("definitions catalog load failed path=%s: %s", path, exc)
        return False
    fingerprint = _definitions_fingerprint(path)
    state = _get_state(cache_key)
    _apply_catalog_to_state(state, copy.deepcopy(tools), fingerprint=fingerprint, config=cfg)
    logger.info(
        "definitions catalog disk_hit path=%s fingerprint=%s tool_count=%d",
        path,
        fingerprint[:24],
        len(tools),
    )
    return True


def load_definitions_catalog_from_disk(config: dict[str, Any] | None = None) -> bool:
    cfg = config or load_config()
    if not uses_definitions_tool_catalog(cfg):
        return False
    cache_key = _cache_key_for_config(cfg)
    state = _get_state(cache_key)
    with _catalog_lock:
        if state.tools:
            return True
    return _load_catalog_from_disk(cache_key, config=cfg)


def _blocking_file_fetch(
    cfg: dict[str, Any],
    cache_key: _DefinitionsCacheKey,
) -> list[dict[str, Any]]:
    path = Path(cache_key.path)
    if not path.is_file():
        return []
    try:
        tools = load_definitions_file(path)
    except (OSError, ValueError) as exc:
        logger.warning("definitions catalog fetch failed path=%s: %s", path, exc)
        state = _get_state(cache_key)
        return _snapshot_tools(state)
    fingerprint = _definitions_fingerprint(path)
    state = _get_state(cache_key)
    _apply_catalog_to_state(state, copy.deepcopy(tools), fingerprint=fingerprint, config=cfg)
    from cyt.tools.definitions_cache_scheduler import start_definitions_cache_scheduler

    start_definitions_cache_scheduler(cfg)
    return copy.deepcopy(tools)


def _ensure_scheduler_started(cfg: dict[str, Any]) -> None:
    from cyt.tools.definitions_cache_scheduler import start_definitions_cache_scheduler

    start_definitions_cache_scheduler(cfg)


def get_definitions_catalog(
    config: dict[str, Any] | None = None,
    *,
    blocking: bool = False,
) -> list[dict[str, Any]] | None:
    """Unified SWR entrypoint: memory snapshot only on hook path (never blocks on refresh)."""
    cfg = config or load_config()
    if not uses_definitions_tool_catalog(cfg):
        return None
    cache_key = _cache_key_for_config(cfg)
    state = _get_state(cache_key)

    with _catalog_lock:
        has_memory = bool(state.tools)

    if not has_memory:
        _load_catalog_from_disk(cache_key, config=cfg)
        with _catalog_lock:
            has_memory = bool(state.tools)

    if not has_memory:
        if blocking:
            return _blocking_file_fetch(cfg, cache_key)
        _ensure_scheduler_started(cfg)
        return []

    _ensure_scheduler_started(cfg)
    return _snapshot_tools(state)


def definitions_catalog_health_snapshot(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config or load_config()
    if not uses_definitions_tool_catalog(cfg):
        return {}
    cache_key = _cache_key_for_config(cfg)
    state = _get_state(cache_key)
    with _catalog_lock:
        tool_count = len(state.tools)
        age_seconds = time.monotonic() - state.updated_at if state.updated_at else None
        fingerprint = state.fingerprint
    payload: dict[str, Any] = {"catalog_tool_count": tool_count}
    if age_seconds is not None:
        payload["catalog_age_seconds"] = round(age_seconds, 1)
    if fingerprint:
        payload["catalog_fingerprint_prefix"] = fingerprint[:24]
    payload["definitions_catalog_path"] = cache_key.path
    return payload


def refresh_definitions_catalog_from_file(config: dict[str, Any] | None = None) -> None:
    """Reload definitions catalog when file fingerprint changes (scheduler helper)."""
    cfg = config or load_config()
    if not uses_definitions_tool_catalog(cfg):
        return
    cache_key = _cache_key_for_config(cfg)
    path = Path(cache_key.path)
    if not path.is_file():
        return
    fingerprint = _definitions_fingerprint(path)
    state = _get_state(cache_key)
    with _catalog_lock:
        if state.fingerprint == fingerprint and state.tools:
            return
    try:
        tools = load_definitions_file(path)
    except (OSError, ValueError) as exc:
        logger.warning("definitions catalog refresh failed path=%s: %s", path, exc)
        return
    _apply_catalog_to_state(state, copy.deepcopy(tools), fingerprint=fingerprint, config=cfg)
