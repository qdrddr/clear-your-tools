"""Integration health flapping detection and persistent gating."""

from __future__ import annotations

import copy
import logging
import threading
import time
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

_HEALTHY_STATUS = "healthy"
_INTEGRATION_KEY_SEP = "/"

_flapping_lock = threading.Lock()
_flapping_states: dict[str, dict[tuple[str, str], IntegrationFlapState]] = {}


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
class IntegrationFlapState:
    status_history: deque[str] = field(default_factory=deque)
    gated: bool = False
    quarantine_until: float = 0.0
    consecutive_healthy: int = 0
    flap_episodes: int = 0


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


def derive_integration_statuses(
    connections: list[dict[str, Any]],
) -> dict[tuple[str, str], str]:
    """Map ``(owner, integration)`` to derived status from connection health."""
    by_integration: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for conn in connections:
        owner = str(conn.get("owner") or "").strip()
        integration = str(conn.get("integration") or "").strip()
        if not owner or not integration:
            continue
        by_integration.setdefault((owner, integration), []).append(conn)

    statuses: dict[tuple[str, str], str] = {}
    for key, group in by_integration.items():
        if any(_connection_is_healthy(conn) for conn in group):
            statuses[key] = _HEALTHY_STATUS
            continue
        worst = _worst_non_healthy_status(group)
        statuses[key] = worst if worst is not None else "unknown"
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


def can_release(state: IntegrationFlapState, *, policy: FlappingPolicy, now: float) -> bool:
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


def gated_integrations(
    slug: str,
    *,
    policy: FlappingPolicy,
    now: float | None = None,
) -> set[tuple[str, str]]:
    """Return integrations currently gated due to flapping."""
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


def snapshot_flapping_states(slug: str) -> dict[tuple[str, str], IntegrationFlapState]:
    with _flapping_lock:
        states = _flapping_states.get(slug, {})
        return {
            key: IntegrationFlapState(
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
) -> dict[tuple[str, str], IntegrationFlapState]:
    """Append latest statuses and apply gating / release rules."""
    if not policy.enabled:
        return {}

    current = now if now is not None else time.monotonic()
    statuses = derive_integration_statuses(connections)

    with _flapping_lock:
        bucket = _flapping_states.setdefault(slug, {})
        seen_keys = set(statuses)
        for key, status in statuses.items():
            state = bucket.get(key)
            if state is None:
                state = IntegrationFlapState(
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
                    "integration flapping gated owner=%s integration=%s penalty_s=%.0f "
                    "degraded=%d transitions=%d episodes=%d reason=flapping",
                    key[0],
                    key[1],
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
                    "integration flapping released owner=%s integration=%s "
                    "consecutive_healthy=%d episodes=%d",
                    key[0],
                    key[1],
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
    integrations: dict[str, Any] = {}
    with _flapping_lock:
        states = _flapping_states.get(slug, {})
        for (owner, integration), state in sorted(states.items()):
            if not state.gated and not state.status_history:
                continue
            key = _integration_key(owner, integration)
            quarantine_until_iso = None
            if state.quarantine_until > 0:
                remaining = max(0.0, state.quarantine_until - time.monotonic())
                quarantine_until_iso = datetime.fromtimestamp(
                    time.time() + remaining,
                    tz=UTC,
                ).isoformat()
            integrations[key] = {
                "status_history": list(state.status_history),
                "gated": state.gated,
                "quarantine_until": quarantine_until_iso,
                "consecutive_healthy": state.consecutive_healthy,
                "flap_episodes": state.flap_episodes,
            }
    if not integrations:
        return {}
    return {"window_size": policy.window_size, "integrations": integrations}


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


def _integration_state_from_disk_item(
    item: dict[str, Any],
    *,
    window_size: int,
) -> IntegrationFlapState:
    history_raw = item.get("status_history")
    history: deque[str] = deque(maxlen=window_size)
    if isinstance(history_raw, list):
        for entry in history_raw:
            history.append(str(entry))
    return IntegrationFlapState(
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
    raw_integrations = payload.get("integrations")
    if not isinstance(raw_integrations, dict):
        return

    window_size = _coerce_int(payload.get("window_size"), policy.window_size)
    restored: dict[tuple[str, str], IntegrationFlapState] = {}
    for key_text, item in raw_integrations.items():
        if not isinstance(item, dict):
            continue
        owner, integration = _parse_integration_key(key_text)
        if owner is None or integration is None:
            continue
        restored[(owner, integration)] = _integration_state_from_disk_item(
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
            "gated_integration_count": 0,
        }
    current = now if now is not None else time.monotonic()
    gated = gated_integrations(slug, policy=policy, now=current)
    entries: list[dict[str, Any]] = []
    with _flapping_lock:
        states = _flapping_states.get(slug, {})
        for owner, integration in sorted(gated):
            state = states.get((owner, integration))
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
                    "owner": owner,
                    "integration": integration,
                    "reason": reason,
                    "seconds_remaining": round(seconds_remaining, 1),
                    "flap_episodes": state.flap_episodes,
                    "consecutive_healthy": state.consecutive_healthy,
                },
            )
    return {
        "flapping_enabled": True,
        "gated_integration_count": len(gated),
        "gated_integrations": entries,
    }


def _connection_is_healthy(conn: dict[str, Any]) -> bool:
    last_health = conn.get("lastHealth")
    if not isinstance(last_health, dict):
        return False
    return last_health.get("status") == _HEALTHY_STATUS


def _worst_non_healthy_status(group: list[dict[str, Any]]) -> str | None:
    statuses: list[str] = []
    for conn in group:
        last_health = conn.get("lastHealth")
        if not isinstance(last_health, dict):
            statuses.append("unknown")
            continue
        status = last_health.get("status")
        if status is None:
            statuses.append("unknown")
        else:
            statuses.append(str(status))
    if not statuses:
        return None
    if any(status == "unhealthy" for status in statuses):
        return "unhealthy"
    if any(status == "degraded" for status in statuses):
        return "degraded"
    return statuses[0]


def _integration_key(owner: str, integration: str) -> str:
    return f"{owner}{_INTEGRATION_KEY_SEP}{integration}"


def _parse_integration_key(key: str) -> tuple[str | None, str | None]:
    if _INTEGRATION_KEY_SEP not in key:
        return None, None
    owner, integration = key.split(_INTEGRATION_KEY_SEP, 1)
    owner = owner.strip()
    integration = integration.strip()
    if not owner or not integration:
        return None, None
    return owner, integration


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
