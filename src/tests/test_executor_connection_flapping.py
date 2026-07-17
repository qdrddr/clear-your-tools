"""Tests for connection health flapping detection and gating."""

from __future__ import annotations

from collections import deque
from typing import Any

from cyt.executor.connection_flapping import (
    ConnectionFlapState,
    FlappingPolicy,
    IntegrationFlapState,
    clear_flapping_cache,
    compute_penalty_seconds,
    flapping_state_to_disk,
    gated_connections,
    is_flapping,
    load_flapping_state_from_disk,
    update_flapping_states,
)
from cyt.executor.connection_health import ConnectionKey

_POLICY = FlappingPolicy(
    window_size=6,
    min_degraded=1,
    min_transitions=2,
    base_quarantine_seconds=90.0,
    per_degraded_seconds=45.0,
    per_transition_seconds=30.0,
    max_quarantine_seconds=600.0,
    recovery_healthy_samples=3,
    per_episode_seconds=60.0,
)
_SLUG = "test-slug"


def setup_function() -> None:
    clear_flapping_cache()


def _connection(
    *,
    owner: str = "org",
    integration: str = "semble_mcp",
    name: str = "default",
    status: str | None = "healthy",
) -> dict[str, Any]:
    conn: dict[str, Any] = {
        "owner": owner,
        "name": name,
        "integration": integration,
    }
    if status is not None:
        conn["lastHealth"] = {"status": status, "checkedAt": 1}
    else:
        conn["lastHealth"] = None
    return conn


def _connections_for_status(
    integration: str,
    status: str,
    *,
    name: str = "default",
) -> list[dict[str, Any]]:
    return [_connection(integration=integration, status=status, name=name)]


def test_is_flapping_detects_h_d_h_pattern() -> None:
    history = ["healthy", "healthy", "degraded", "healthy"]
    assert is_flapping(history, policy=_POLICY) is True


def test_is_flapping_ignores_stable_healthy_window() -> None:
    history = ["healthy", "healthy", "healthy", "healthy"]
    assert is_flapping(history, policy=_POLICY) is False


def test_is_flapping_ignores_sustained_outage_recovery() -> None:
    history = ["degraded", "degraded", "degraded", "healthy"]
    assert is_flapping(history, policy=_POLICY) is False


def test_compute_penalty_scales_with_degraded_and_transitions() -> None:
    history = ["healthy", "degraded", "healthy"]
    penalty = compute_penalty_seconds(history, policy=_POLICY, flap_episodes=1)
    assert penalty == 90.0 + 45.0 + (2 * 30.0)


def test_compute_penalty_respects_max_cap() -> None:
    history = ["healthy", "degraded", "healthy", "degraded", "healthy"]
    penalty = compute_penalty_seconds(history, policy=_POLICY, flap_episodes=10)
    assert penalty == _POLICY.max_quarantine_seconds


def test_update_flapping_states_gates_on_first_flap() -> None:
    now = 1000.0
    key = ConnectionKey("org", "flappy_mcp", "default")
    update_flapping_states(
        _SLUG,
        _connections_for_status("flappy_mcp", "healthy"),
        policy=_POLICY,
        now=now,
    )
    update_flapping_states(
        _SLUG,
        _connections_for_status("flappy_mcp", "degraded"),
        policy=_POLICY,
        now=now + 10,
    )
    update_flapping_states(
        _SLUG,
        _connections_for_status("flappy_mcp", "healthy"),
        policy=_POLICY,
        now=now + 20,
    )

    gated = gated_connections(_SLUG, policy=_POLICY, now=now + 20)
    assert gated == {key}


def test_connection_flapping_gates_only_that_connection() -> None:
    now = 1500.0
    flappy_key = ConnectionKey("org", "lean_ctx_mcp", "otherconn")
    healthy_key = ConnectionKey("org", "lean_ctx_mcp", "localleanctxmcp")
    for index, status in enumerate(["healthy", "degraded", "healthy"]):
        update_flapping_states(
            _SLUG,
            [
                _connection(integration="lean_ctx_mcp", name="localleanctxmcp", status="healthy"),
                _connection(integration="lean_ctx_mcp", name="otherconn", status=status),
            ],
            policy=_POLICY,
            now=now + (index * 10),
        )
    gated = gated_connections(_SLUG, policy=_POLICY, now=now + 20)
    assert flappy_key in gated
    assert healthy_key not in gated


def test_continuous_flapping_keeps_connection_gated() -> None:
    now = 2000.0
    key = ConnectionKey("org", "flappy_mcp", "default")
    sequence = ["healthy", "degraded", "healthy", "degraded", "healthy", "degraded", "healthy"]
    for index, status in enumerate(sequence):
        update_flapping_states(
            _SLUG,
            _connections_for_status("flappy_mcp", status),
            policy=_POLICY,
            now=now + (index * 10),
        )
        gated = gated_connections(_SLUG, policy=_POLICY, now=now + (index * 10))
        if index >= 2:
            assert key in gated

    far_future = now + 10_000
    assert key in gated_connections(_SLUG, policy=_POLICY, now=far_future)


def test_sustained_outage_recovery_does_not_gate() -> None:
    now = 3000.0
    for index, status in enumerate(["degraded", "degraded", "degraded", "healthy"]):
        update_flapping_states(
            _SLUG,
            _connections_for_status("recover_mcp", status),
            policy=_POLICY,
            now=now + (index * 10),
        )
    assert gated_connections(_SLUG, policy=_POLICY, now=now + 40) == set()


def test_release_requires_stable_healthy_streak_and_cooldown() -> None:
    now = 4000.0
    key = ConnectionKey("org", "mcp", "default")
    update_flapping_states(
        _SLUG,
        _connections_for_status("mcp", "healthy"),
        policy=_POLICY,
        now=now,
    )
    update_flapping_states(
        _SLUG,
        _connections_for_status("mcp", "degraded"),
        policy=_POLICY,
        now=now + 10,
    )
    update_flapping_states(
        _SLUG,
        _connections_for_status("mcp", "healthy"),
        policy=_POLICY,
        now=now + 20,
    )
    assert key in gated_connections(_SLUG, policy=_POLICY, now=now + 20)

    stable_start = now + 500
    for offset in (0, 10):
        update_flapping_states(
            _SLUG,
            _connections_for_status("mcp", "healthy"),
            policy=_POLICY,
            now=stable_start + offset,
        )
    assert key in gated_connections(_SLUG, policy=_POLICY, now=stable_start + 10)

    update_flapping_states(
        _SLUG,
        _connections_for_status("mcp", "healthy"),
        policy=_POLICY,
        now=stable_start + 20,
    )
    assert gated_connections(_SLUG, policy=_POLICY, now=stable_start + 20) == set()


def test_flapping_state_disk_round_trip() -> None:
    clear_flapping_cache()
    now = 5000.0
    key = ConnectionKey("org", "disk_mcp", "default")
    update_flapping_states(
        _SLUG,
        _connections_for_status("disk_mcp", "healthy"),
        policy=_POLICY,
        now=now,
    )
    update_flapping_states(
        _SLUG,
        _connections_for_status("disk_mcp", "degraded"),
        policy=_POLICY,
        now=now + 10,
    )
    update_flapping_states(
        _SLUG,
        _connections_for_status("disk_mcp", "healthy"),
        policy=_POLICY,
        now=now + 20,
    )

    payload = flapping_state_to_disk(_SLUG, policy=_POLICY)
    assert payload
    assert "org/disk_mcp/default" in payload["connections"]

    clear_flapping_cache()
    load_flapping_state_from_disk(_SLUG, payload, policy=_POLICY)
    gated = gated_connections(_SLUG, policy=_POLICY, now=now + 20)
    assert gated == {key}


def test_disabled_policy_never_gates() -> None:
    disabled = FlappingPolicy(enabled=False)
    now = 6000.0
    for index, status in enumerate(["healthy", "degraded", "healthy"]):
        update_flapping_states(
            _SLUG,
            _connections_for_status("mcp", status),
            policy=disabled,
            now=now + (index * 10),
        )
    assert gated_connections(_SLUG, policy=disabled, now=now + 20) == set()


def test_manual_state_can_release_after_cooldown_and_stability() -> None:
    state = IntegrationFlapState(
        status_history=deque(["healthy", "healthy", "healthy"], maxlen=6),
        gated=True,
        quarantine_until=100.0,
        consecutive_healthy=3,
        flap_episodes=1,
    )
    assert is_flapping(list(state.status_history), policy=_POLICY) is False
    assert state.consecutive_healthy >= _POLICY.recovery_healthy_samples
    assert 150.0 >= state.quarantine_until
    assert isinstance(state, ConnectionFlapState)
