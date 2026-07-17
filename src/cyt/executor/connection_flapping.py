"""Connection health flapping detection and persistent gating."""

from __future__ import annotations

import copy
import logging
import threading
import time
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cyt.executor.connection_health import ConnectionKey

logger = logging.getLogger(__name__)

_HEALTHY_STATUS = "healthy"
_CONNECTION_KEY_SEP = "/"

_flapping_lock = threading.Lock()
_flapping_states: dict[str, dict[ConnectionKey, ConnectionFlapState]] = {}


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
class ConnectionFlapState:
    status_history: deque[str] = field(default_factory=deque)
    gated: bool = False
    quarantine_until: float = 0.0
    consecutive_healthy: int = 0
    flap_episodes: int = 0


# Backward-compatible alias for tests importing IntegrationFlapState.
IntegrationFlapState = ConnectionFlapState


def clear_flapping_cache() -> None:
    """Reset in-process flapping state (for tests)."""
    with _flapping_lock:
        _flapping_states.clear()


def flapping_policy_from_config(raw: dict[str, Any] | None) -> FlappingPolicy:
    """Build policy from merged config dict with defaults."""
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


def derive_connection_statuses(
    connections: list[dict[str, Any]],
) -> dict[ConnectionKey, str]:
    """Map each connection to its own ``lastHealth.status`` (no integration rollup)."""
    from cyt.executor.connection_health import connection_key_from_dict

    statuses: dict[ConnectionKey, str] = {}
    for conn in connections:
        key = connection_key_from_dict(conn)
        if key is None:
            continue
        last_health = conn.get("lastHealth")
        if isinstance(last_health, dict) and last_health.get("status"):
            statuses[key] = str(last_health["status"])
        else:
            statuses[key] = "unknown"
    return statuses


def is_flapping(history: Sequence[str], *, policy: FlappingPolicy) -> bool:
    """Return True when the rolling window shows an H-D-H style oscillation."""
    if len(history) < 2:
        return False

    non_healthy_count = sum(1 for status in history if status != _HEALTHY_STATUS)
    if non_healthy_count < policy.min_degraded:
        return False

    transitions = sum(1 for index in range(1, len(history)) if history[index] != history[index - 1])
    if transitions < policy.min_transitions:
        return False

    for index, status in enumerate(history):
        if status == _HEALTHY_STATUS:
            continue
        has_healthy_before = any(history[j] == _HEALTHY_STATUS for j in range(index))
        has_healthy_after = any(
            history[j] == _HEALTHY_STATUS for j in range(index + 1, len(history))
        )
        if has_healthy_before and has_healthy_after:
            return True
    return False


def compute_penalty_seconds(
    history: Sequence[str],
    *,
    policy: FlappingPolicy,
    flap_episodes: int,
) -> float:
    """Scaled cooldown for the current flap episode."""
    degraded_count = sum(1 for status in history if status != _HEALTHY_STATUS)
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


def can_release(state: ConnectionFlapState, *, policy: FlappingPolicy, now: float) -> bool:
    """True when stability and cooldown requirements are satisfied."""
    if now < state.quarantine_until:
        return False
    if state.consecutive_healthy < policy.recovery_healthy_samples:
        return False
    history = list(state.status_history)
    if len(history) < policy.recovery_healthy_samples:
        return False
    sample_count = policy.recovery_healthy_samples
    recent = history[-sample_count:]
    return not is_flapping(recent, policy=policy)


def gated_connections(
    slug: str,
    *,
    policy: FlappingPolicy,
    now: float | None = None,
) -> set[ConnectionKey]:
    """Return connections currently gated due to flapping."""

    if not policy.enabled:
        return set()
    current = now if now is not None else time.monotonic()
    with _flapping_lock:
        states = _flapping_states.get(slug, {})
        return {
            key
            for key, state in states.items()
            if state.gated and not can_release(state, policy=policy, now=current)
        }


def snapshot_flapping_states(slug: str) -> dict[ConnectionKey, ConnectionFlapState]:

    with _flapping_lock:
        states = _flapping_states.get(slug, {})
        return {
            key: ConnectionFlapState(
                status_history=deque(state.status_history, maxlen=state.status_history.maxlen),
                gated=state.gated,
                quarantine_until=state.quarantine_until,
                consecutive_healthy=state.consecutive_healthy,
                flap_episodes=state.flap_episodes,
            )
            for key, state in states.items()
        }


def update_flapping_states(
    slug: str,
    connections: list[dict[str, Any]],
    *,
    policy: FlappingPolicy,
    now: float | None = None,
) -> dict[ConnectionKey, ConnectionFlapState]:
    """Append latest per-connection statuses and apply gating / release rules."""

    if not policy.enabled:
        return {}

    current = now if now is not None else time.monotonic()
    statuses = derive_connection_statuses(connections)

    with _flapping_lock:
        bucket = _flapping_states.setdefault(slug, {})
        seen_keys = set(statuses)
        for key, status in statuses.items():
            state = bucket.get(key)
            if state is None:
                state = ConnectionFlapState(
                    status_history=deque(maxlen=policy.window_size),
                )
                bucket[key] = state

            history = state.status_history
            history.append(status)

            if status == _HEALTHY_STATUS:
                state.consecutive_healthy += 1
            else:
                state.consecutive_healthy = 0

            flapping = is_flapping(history, policy=policy)
            previous = history[-2] if len(history) >= 2 else None
            recently_unstable = status != _HEALTHY_STATUS or previous != _HEALTHY_STATUS
            active_flapping = flapping and recently_unstable
            if active_flapping:
                if not state.gated:
                    state.flap_episodes = 1
                else:
                    state.flap_episodes += 1
                state.gated = True
                state.consecutive_healthy = 0
                penalty = compute_penalty_seconds(
                    history,
                    policy=policy,
                    flap_episodes=state.flap_episodes,
                )
                state.quarantine_until = max(state.quarantine_until, current + penalty)
                logger.info(
                    "connection flapping gated owner=%s integration=%s name=%s penalty_s=%.0f "
                    "degraded=%d transitions=%d episodes=%d reason=flapping",
                    key.owner,
                    key.integration,
                    key.name,
                    penalty,
                    sum(1 for item in history if item != _HEALTHY_STATUS),
                    sum(
                        1
                        for index in range(1, len(history))
                        if history[index] != history[index - 1]
                    ),
                    state.flap_episodes,
                )
            elif state.gated and can_release(state, policy=policy, now=current):
                logger.info(
                    "connection flapping released owner=%s integration=%s name=%s "
                    "consecutive_healthy=%d episodes=%d",
                    key.owner,
                    key.integration,
                    key.name,
                    state.consecutive_healthy,
                    state.flap_episodes,
                )
                state.gated = False
                state.flap_episodes = 0
                state.quarantine_until = 0.0
                state.status_history.clear()

        for key in list(bucket):
            if key not in seen_keys and not bucket[key].gated:
                del bucket[key]

        return copy.deepcopy(bucket)


def flapping_state_to_disk(
    slug: str,
    *,
    policy: FlappingPolicy,
) -> dict[str, Any]:
    """Serialize flapping state for the connections_health envelope."""
    connections: dict[str, Any] = {}
    with _flapping_lock:
        states = _flapping_states.get(slug, {})
        for key, state in sorted(states.items(), key=lambda item: _connection_key(item[0])):
            if not state.gated and not state.status_history:
                continue
            key_text = _connection_key(key)
            quarantine_until_iso = None
            if state.quarantine_until > 0:
                remaining = max(0.0, state.quarantine_until - time.monotonic())
                quarantine_until_iso = datetime.fromtimestamp(
                    time.time() + remaining,
                    tz=UTC,
                ).isoformat()
            connections[key_text] = {
                "status_history": list(state.status_history),
                "gated": state.gated,
                "quarantine_until": quarantine_until_iso,
                "consecutive_healthy": state.consecutive_healthy,
                "flap_episodes": state.flap_episodes,
            }
    if not connections:
        return {}
    return {"window_size": policy.window_size, "connections": connections}


def _quarantine_until_from_disk_item(item: dict[str, Any]) -> float:
    quarantine_iso = item.get("quarantine_until")
    if not isinstance(quarantine_iso, str) or not quarantine_iso:
        return 0.0
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


def _connection_state_from_disk_item(
    item: dict[str, Any],
    *,
    window_size: int,
) -> ConnectionFlapState:
    history_raw = item.get("status_history")
    history: deque[str] = deque(maxlen=window_size)
    if isinstance(history_raw, list):
        for entry in history_raw:
            history.append(str(entry))
    return ConnectionFlapState(
        status_history=history,
        gated=bool(item.get("gated")),
        quarantine_until=_quarantine_until_from_disk_item(item),
        consecutive_healthy=max(0, _coerce_int(item.get("consecutive_healthy"), 0)),
        flap_episodes=max(0, _coerce_int(item.get("flap_episodes"), 0)),
    )


def load_flapping_state_from_disk(
    slug: str,
    payload: dict[str, Any] | None,
    *,
    policy: FlappingPolicy,
) -> None:
    """Restore flapping state from disk payload."""

    if not payload or not policy.enabled:
        return
    raw_connections = payload.get("connections")
    if not isinstance(raw_connections, dict):
        raw_connections = payload.get("integrations")
    if not isinstance(raw_connections, dict):
        return

    window_size = _coerce_int(payload.get("window_size"), policy.window_size)
    restored: dict[ConnectionKey, ConnectionFlapState] = {}
    for key_text, item in raw_connections.items():
        if not isinstance(item, dict):
            continue
        key = _parse_connection_key(key_text)
        if key is None:
            continue
        restored[key] = _connection_state_from_disk_item(
            item,
            window_size=window_size,
        )

    with _flapping_lock:
        _flapping_states[slug] = restored


def flapping_snapshot_fields(
    slug: str,
    *,
    policy: FlappingPolicy,
    now: float | None = None,
) -> dict[str, Any]:
    """Observability fields for health snapshots."""
    if not policy.enabled:
        return {
            "flapping_enabled": False,
            "gated_connection_count": 0,
        }
    current = now if now is not None else time.monotonic()
    gated = gated_connections(slug, policy=policy, now=current)
    entries: list[dict[str, Any]] = []
    with _flapping_lock:
        states = _flapping_states.get(slug, {})
        for key in sorted(gated, key=_connection_key):
            state = states.get(key)
            if state is None:
                continue
            history = list(state.status_history)
            if is_flapping(history, policy=policy):
                reason = "flapping"
            elif state.consecutive_healthy < policy.recovery_healthy_samples:
                reason = "stabilizing"
            else:
                reason = "cooldown"
            seconds_remaining = max(0.0, state.quarantine_until - current)
            entries.append(
                {
                    "owner": key.owner,
                    "integration": key.integration,
                    "connection": key.name,
                    "reason": reason,
                    "seconds_remaining": round(seconds_remaining, 1),
                    "flap_episodes": state.flap_episodes,
                    "consecutive_healthy": state.consecutive_healthy,
                },
            )
    return {
        "flapping_enabled": True,
        "gated_connection_count": len(gated),
        "gated_connections": entries,
    }


def _connection_key(key: ConnectionKey) -> str:
    return f"{key.owner}{_CONNECTION_KEY_SEP}{key.integration}{_CONNECTION_KEY_SEP}{key.name}"


def _parse_connection_key(key: str) -> ConnectionKey | None:
    from cyt.executor.connection_health import ConnectionKey

    parts = key.split(_CONNECTION_KEY_SEP)
    if len(parts) != 3:
        return None
    owner, integration, name = (part.strip() for part in parts)
    if not owner or not integration or not name:
        return None
    return ConnectionKey(owner=owner, integration=integration, name=name)


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
