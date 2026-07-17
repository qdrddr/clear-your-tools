"""Session health cache for MCPC hook injection."""

from __future__ import annotations

import copy
import hashlib
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, cast

from cyt.mcpc.cli import run_mcpc_json
from cyt.mcpc.runtime import connection_health_flapping_settings, load_config
from cyt.mcpc.session_flapping import (
    clear_flapping_cache,
    flapping_policy_from_config,
    flapping_snapshot_fields,
    flapping_state_to_disk,
    gated_sessions,
    load_flapping_state_from_disk,
    update_flapping_states,
)

logger = logging.getLogger(__name__)

_LIVE_STATUS = "live"

_health_lock = threading.Lock()
_health_states: dict[str, SessionHealthSnapshot] = {}
_permissive_filter_logged: set[str] = set()
_debug_disk_enabled = False


@dataclass(frozen=True)
class SessionKey:
    name: str


@dataclass
class SessionHealthSnapshot:
    sessions: dict[SessionKey, dict[str, Any]] = field(default_factory=dict)
    live_sessions: set[SessionKey] = field(default_factory=set)
    updated_at: float = 0.0
    loaded: bool = False


def set_mcpc_debug_disk(enabled: bool) -> None:
    global _debug_disk_enabled
    _debug_disk_enabled = enabled


def debug_disk_enabled() -> bool:
    return _debug_disk_enabled


def clear_session_health_cache() -> None:
    with _health_lock:
        _health_states.clear()
        _permissive_filter_logged.clear()
    clear_flapping_cache()


def session_key_from_dict(session: dict[str, Any]) -> SessionKey | None:
    name = str(session.get("name") or "").strip()
    if not name:
        return None
    return SessionKey(name=name)


def session_key_from_tool(tool: dict[str, Any]) -> SessionKey | None:
    name = str(tool.get("mcpc_session") or "").strip()
    if not name:
        return None
    return SessionKey(name=name)


def sessions_list_to_dict(sessions: list[dict[str, Any]]) -> dict[SessionKey, dict[str, Any]]:
    result: dict[SessionKey, dict[str, Any]] = {}
    for session in sessions:
        key = session_key_from_dict(session)
        if key is None:
            continue
        result[key] = session
    return result


def build_live_sessions(
    sessions: dict[SessionKey, dict[str, Any]] | list[dict[str, Any]],
) -> set[SessionKey]:
    if isinstance(sessions, list):
        sessions = sessions_list_to_dict(sessions)
    live: set[SessionKey] = set()
    for key, session in sessions.items():
        if _session_is_live(session):
            live.add(key)
    return live


def sessions_fingerprint(sessions: dict[SessionKey, dict[str, Any]]) -> str:
    keys = sorted(key.name for key in sessions)
    payload = "\n".join(keys)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def eligible_sessions(
    *,
    live_sessions: set[SessionKey],
    gated_sessions: set[SessionKey],
) -> set[SessionKey]:
    return live_sessions - gated_sessions


def _session_is_live(session: dict[str, Any]) -> bool:
    return str(session.get("status") or "").strip().lower() == _LIVE_STATUS


def filter_catalog_by_session_health(
    tools: list[dict[str, Any]],
    slug: str,
    *,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    snapshot = snapshot_health_for_catalog(slug)
    if snapshot is None or not snapshot.loaded:
        _log_permissive_filter_once(slug)
        return tools
    cfg = config or load_config()
    policy = flapping_policy_from_config(connection_health_flapping_settings(cfg))
    gated = gated_sessions(slug, policy=policy)
    filtered: list[dict[str, Any]] = []
    for tool in tools:
        key = session_key_from_tool(tool)
        if key is None:
            continue
        if key in gated:
            continue
        if key not in snapshot.live_sessions:
            continue
        filtered.append(tool)
    return filtered


def snapshot_health_for_catalog(slug: str) -> SessionHealthSnapshot | None:
    with _health_lock:
        snapshot = _health_states.get(slug)
        if snapshot is None:
            return None
        return SessionHealthSnapshot(
            sessions=copy.deepcopy(snapshot.sessions),
            live_sessions=set(snapshot.live_sessions),
            updated_at=snapshot.updated_at,
            loaded=snapshot.loaded,
        )


def session_health_snapshot_fields(
    slug: str,
    *,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot = snapshot_health_for_catalog(slug)
    cfg = config or load_config()
    policy = flapping_policy_from_config(connection_health_flapping_settings(cfg))
    fields: dict[str, Any] = {
        "mcpc_catalog_slug": slug,
        "session_health_loaded": bool(snapshot and snapshot.loaded),
    }
    if snapshot is not None and snapshot.loaded:
        fields["live_session_count"] = len(snapshot.live_sessions)
        fields["tracked_session_count"] = len(snapshot.sessions)
    fields.update(flapping_snapshot_fields(slug, policy=policy))
    return fields


def health_snapshot_to_disk(
    snapshot: SessionHealthSnapshot,
    *,
    slug: str,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = config or load_config()
    policy = flapping_policy_from_config(connection_health_flapping_settings(cfg))
    flapping_block = flapping_state_to_disk(slug, policy=policy)
    sessions_block: dict[str, Any] = {}
    for key, session in sorted(snapshot.sessions.items(), key=lambda item: item[0].name):
        sessions_block[key.name] = {
            "status": str(session.get("status") or "unknown"),
        }
    payload: dict[str, Any] = {"sessions": sessions_block}
    if flapping_block:
        payload["flapping"] = flapping_block
    return payload


def apply_health_snapshot(slug: str, snapshot: SessionHealthSnapshot) -> None:
    with _health_lock:
        _health_states[slug] = snapshot


def refresh_session_health(
    *,
    executable: str,
    slug: str,
    config: dict[str, Any] | None = None,
) -> SessionHealthSnapshot:
    cfg = config or load_config()
    payload = run_mcpc_json(executable, [])
    sessions_raw: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        payload_dict = cast(dict[str, Any], payload)
        raw = payload_dict.get("sessions")
        if isinstance(raw, list):
            sessions_raw = [cast(dict[str, Any], item) for item in raw if isinstance(item, dict)]

    sessions = sessions_list_to_dict(sessions_raw)
    live = build_live_sessions(sessions)
    policy = flapping_policy_from_config(connection_health_flapping_settings(cfg))
    update_flapping_states(slug, sessions_raw, policy=policy)

    snapshot = SessionHealthSnapshot(
        sessions=sessions,
        live_sessions=live,
        updated_at=time.monotonic(),
        loaded=True,
    )
    apply_health_snapshot(slug, snapshot)
    return snapshot


def load_session_health_from_disk(
    slug: str,
    payload: dict[str, Any] | None,
    *,
    config: dict[str, Any] | None = None,
) -> None:
    if not payload:
        return
    cfg = config or load_config()
    policy = flapping_policy_from_config(connection_health_flapping_settings(cfg))
    flapping_raw = payload.get("flapping")
    if isinstance(flapping_raw, dict):
        load_flapping_state_from_disk(slug, flapping_raw, policy=policy)

    raw_sessions = payload.get("sessions")
    if not isinstance(raw_sessions, dict):
        return
    sessions: dict[SessionKey, dict[str, Any]] = {}
    for name, item in raw_sessions.items():
        if not isinstance(item, dict):
            continue
        key = SessionKey(name=str(name).strip())
        if not key.name:
            continue
        sessions[key] = {"name": key.name, "status": str(item.get("status") or "unknown")}
    snapshot = SessionHealthSnapshot(
        sessions=sessions,
        live_sessions=build_live_sessions(sessions),
        updated_at=time.monotonic(),
        loaded=True,
    )
    apply_health_snapshot(slug, snapshot)


def session_fingerprint_for_slug(slug: str) -> str:
    snapshot = snapshot_health_for_catalog(slug)
    if snapshot is None or not snapshot.loaded:
        return ""
    return sessions_fingerprint(snapshot.sessions)


def _log_permissive_filter_once(slug: str) -> None:
    with _health_lock:
        if slug in _permissive_filter_logged:
            return
        _permissive_filter_logged.add(slug)
    logger.debug("mcpc session health not loaded slug=%s; permissive catalog filter", slug)


def eligible_session_names(slug: str, *, config: dict[str, Any] | None = None) -> set[str]:
    snapshot = snapshot_health_for_catalog(slug)
    cfg = config or load_config()
    policy = flapping_policy_from_config(connection_health_flapping_settings(cfg))
    gated = gated_sessions(slug, policy=policy)
    if snapshot is None or not snapshot.loaded:
        return set()
    eligible = eligible_sessions(live_sessions=snapshot.live_sessions, gated_sessions=gated)
    return {key.name for key in eligible}
