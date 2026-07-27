"""Server health flapping detection for Cloudflare portal upstream servers."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

_ENABLED_STATUS = "enabled"

_flapping_lock = threading.Lock()
_flapping_states: dict[str, dict[str, ServerFlapState]] = {}


@dataclass(frozen=True)
class ServerKey:
    server_id: str


@dataclass(frozen=True)
class FlappingPolicy:
    enabled: bool = True
    window_size: int = 12
    min_degraded: int = 1
    min_transitions: int = 2
    base_quarantine_seconds: float = 90.0
    per_degraded_seconds: float = 45.0
    per_transition_seconds: float = 30.0
    max_quarantine_seconds: float = 600.0
    recovery_healthy_samples: int = 6
    per_episode_seconds: float = 60.0


@dataclass
class ServerFlapState:
    status_history: deque[str] = field(default_factory=deque)
    gated: bool = False
    quarantine_until: float = 0.0
    consecutive_healthy: int = 0
    flap_episodes: int = 0


def clear_flapping_cache() -> None:
    with _flapping_lock:
        _flapping_states.clear()


def flapping_policy_from_config(raw: dict[str, Any] | None) -> FlappingPolicy:
    if not raw:
        return FlappingPolicy()
    return FlappingPolicy(
        enabled=_coerce_bool(raw.get("enabled"), True),
        window_size=max(2, _coerce_int(raw.get("window_size"), 12)),
        min_degraded=max(1, _coerce_int(raw.get("min_degraded"), 1)),
        min_transitions=max(1, _coerce_int(raw.get("min_transitions"), 2)),
        base_quarantine_seconds=max(0.0, _coerce_float(raw.get("base_quarantine_seconds"), 90.0)),
        per_degraded_seconds=max(0.0, _coerce_float(raw.get("per_degraded_seconds"), 45.0)),
        per_transition_seconds=max(0.0, _coerce_float(raw.get("per_transition_seconds"), 30.0)),
        max_quarantine_seconds=max(0.0, _coerce_float(raw.get("max_quarantine_seconds"), 600.0)),
        recovery_healthy_samples=max(1, _coerce_int(raw.get("recovery_healthy_samples"), 6)),
        per_episode_seconds=max(0.0, _coerce_float(raw.get("per_episode_seconds"), 60.0)),
    )


def derive_server_statuses(servers: list[dict[str, Any]]) -> dict[ServerKey, str]:
    statuses: dict[ServerKey, str] = {}
    for server in servers:
        server_id = str(
            server.get("id") or server.get("server_id") or server.get("name") or "",
        ).strip()
        if not server_id:
            continue
        enabled = server.get("enabled")
        if enabled is None:
            enabled = server.get("is_enabled", True)
        status = _ENABLED_STATUS if bool(enabled) else "disabled"
        statuses[ServerKey(server_id=server_id)] = status
    return statuses


def is_flapping(history: Sequence[str], *, policy: FlappingPolicy) -> bool:
    if len(history) < 2:
        return False
    non_enabled_count = sum(1 for status in history if status != _ENABLED_STATUS)
    if non_enabled_count < policy.min_degraded:
        return False
    transitions = sum(1 for index in range(1, len(history)) if history[index] != history[index - 1])
    if transitions < policy.min_transitions:
        return False
    for index, status in enumerate(history):
        if status == _ENABLED_STATUS:
            continue
        has_enabled_before = any(history[j] == _ENABLED_STATUS for j in range(index))
        has_enabled_after = any(
            history[j] == _ENABLED_STATUS for j in range(index + 1, len(history))
        )
        if has_enabled_before and has_enabled_after:
            return True
    return False


def compute_penalty_seconds(
    history: Sequence[str],
    *,
    policy: FlappingPolicy,
    flap_episodes: int,
) -> float:
    degraded_count = sum(1 for status in history if status != _ENABLED_STATUS)
    transition_count = sum(
        1 for index in range(1, len(history)) if history[index] != history[index - 1]
    )
    episode_bonus = max(0, flap_episodes - 1) * policy.per_episode_seconds
    raw = (
        policy.base_quarantine_seconds
        + degraded_count * policy.per_degraded_seconds
        + transition_count * policy.per_transition_seconds
        + episode_bonus
    )
    return min(policy.max_quarantine_seconds, raw)


def update_flapping_states(
    slug: str,
    statuses: dict[ServerKey, str],
    *,
    policy: FlappingPolicy,
) -> None:
    now = time.monotonic()
    with _flapping_lock:
        bucket = _flapping_states.setdefault(slug, {})
        for key, status in statuses.items():
            state = bucket.setdefault(key.server_id, ServerFlapState())
            state.status_history.append(status)
            while len(state.status_history) > policy.window_size:
                state.status_history.popleft()
            if state.gated and now >= state.quarantine_until:
                state.gated = False
            if status == _ENABLED_STATUS:
                state.consecutive_healthy += 1
            else:
                state.consecutive_healthy = 0
            if state.gated and state.consecutive_healthy >= policy.recovery_healthy_samples:
                state.gated = False
                state.consecutive_healthy = 0
                logger.info(
                    "cloudflare server flapping recovered slug=%s server=%s",
                    slug,
                    key.server_id,
                )
            if not policy.enabled or state.gated:
                continue
            history = list(state.status_history)
            if is_flapping(history, policy=policy):
                state.flap_episodes += 1
                penalty = compute_penalty_seconds(
                    history,
                    policy=policy,
                    flap_episodes=state.flap_episodes,
                )
                state.gated = True
                state.quarantine_until = now + penalty
                state.consecutive_healthy = 0
                logger.warning(
                    "cloudflare server flapping quarantine slug=%s server=%s seconds=%.1f",
                    slug,
                    key.server_id,
                    penalty,
                )


def gated_servers(slug: str) -> set[ServerKey]:
    now = time.monotonic()
    with _flapping_lock:
        bucket = _flapping_states.get(slug, {})
        return {
            ServerKey(server_id=server_id)
            for server_id, state in bucket.items()
            if state.gated and now < state.quarantine_until
        }


def flapping_snapshot_fields(slug: str) -> dict[str, Any]:
    with _flapping_lock:
        bucket = _flapping_states.get(slug, {})
        gated = [
            server_id
            for server_id, state in bucket.items()
            if state.gated and time.monotonic() < state.quarantine_until
        ]
    return {"gated_servers": sorted(gated), "gated_server_count": len(gated)}


def load_flapping_state_from_disk(slug: str, payload: dict[str, Any] | None) -> None:
    if not payload:
        return
    servers = payload.get("servers")
    if not isinstance(servers, dict):
        return
    with _flapping_lock:
        bucket = _flapping_states.setdefault(slug, {})
        for server_id, raw in servers.items():
            if not isinstance(raw, dict):
                continue
            state = ServerFlapState(
                gated=bool(raw.get("gated")),
                quarantine_until=_quarantine_until_from_disk_item(raw),
                consecutive_healthy=int(raw.get("consecutive_healthy") or 0),
                flap_episodes=int(raw.get("flap_episodes") or 0),
            )
            history = raw.get("status_history")
            if isinstance(history, list):
                state.status_history = deque(str(item) for item in history)
            bucket[str(server_id)] = state


def flapping_state_to_disk(slug: str) -> dict[str, Any]:
    with _flapping_lock:
        bucket = _flapping_states.get(slug, {})
        servers = {
            server_id: {
                "gated": state.gated,
                "quarantine_until": _quarantine_until_to_disk_value(state),
                "consecutive_healthy": state.consecutive_healthy,
                "flap_episodes": state.flap_episodes,
                "status_history": list(state.status_history),
            }
            for server_id, state in bucket.items()
        }
    return {"servers": servers}


def _quarantine_until_to_disk_value(state: ServerFlapState) -> str | None:
    if state.quarantine_until <= 0:
        return None
    remaining = max(0.0, state.quarantine_until - time.monotonic())
    if remaining <= 0:
        return None
    return datetime.fromtimestamp(time.time() + remaining, tz=UTC).isoformat()


def _quarantine_until_from_disk_item(item: dict[str, Any]) -> float:
    quarantine_iso = item.get("quarantine_until")
    if isinstance(quarantine_iso, str) and quarantine_iso:
        try:
            deadline = datetime.fromisoformat(quarantine_iso)
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=UTC)
            remaining = deadline.timestamp() - time.time()
            if remaining > 0:
                return time.monotonic() + remaining
        except ValueError:
            return 0.0
        return 0.0
    # Legacy payloads stored raw monotonic values; they are not portable across restarts.
    if isinstance(quarantine_iso, (int, float)):
        return 0.0
    return 0.0


def _coerce_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _coerce_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def _coerce_float(value: object, default: float) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return default
    return default
