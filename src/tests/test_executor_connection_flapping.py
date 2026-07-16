"""Tests for integration health flapping detection and gating."""

from __future__ import annotations

from collections import deque
from typing import Any

from cyt.tools.sources.executor_connection_flapping import (
    FlappingPolicy,
    IntegrationFlapState,
    clear_flapping_cache,
    compute_penalty_seconds,
    flapping_state_to_disk,
    gated_integrations,
    is_flapping,
    load_flapping_state_from_disk,
    update_flapping_states,
)

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


def _connections_for_status(integration: str, status: str) -> list[dict[str, Any]]:
    return [_connection(integration=integration, status=status)]


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

    gated = gated_integrations(_SLUG, policy=_POLICY, now=now + 20)
    assert gated == {("org", "flappy_mcp")}


def test_continuous_flapping_keeps_integration_gated() -> None:
    now = 2000.0
    sequence = ["healthy", "degraded", "healthy", "degraded", "healthy", "degraded", "healthy"]
    for index, status in enumerate(sequence):
        update_flapping_states(
            _SLUG,
            _connections_for_status("flappy_mcp", status),
            policy=_POLICY,
            now=now + (index * 10),
        )
        gated = gated_integrations(_SLUG, policy=_POLICY, now=now + (index * 10))
        if index >= 2:
            assert ("org", "flappy_mcp") in gated

    far_future = now + 10_000
    assert ("org", "flappy_mcp") in gated_integrations(_SLUG, policy=_POLICY, now=far_future)


def test_sustained_outage_recovery_does_not_gate() -> None:
    now = 3000.0
    for index, status in enumerate(["degraded", "degraded", "degraded", "healthy"]):
        update_flapping_states(
            _SLUG,
            _connections_for_status("recover_mcp", status),
            policy=_POLICY,
            now=now + (index * 10),
        )
    assert gated_integrations(_SLUG, policy=_POLICY, now=now + 40) == set()


def test_release_requires_stable_healthy_streak_and_cooldown() -> None:
    now = 4000.0
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
    assert ("org", "mcp") in gated_integrations(_SLUG, policy=_POLICY, now=now + 20)

    stable_start = now + 500
    for offset in (0, 10):
        update_flapping_states(
            _SLUG,
            _connections_for_status("mcp", "healthy"),
            policy=_POLICY,
            now=stable_start + offset,
        )
    assert ("org", "mcp") in gated_integrations(_SLUG, policy=_POLICY, now=stable_start + 10)

    update_flapping_states(
        _SLUG,
        _connections_for_status("mcp", "healthy"),
        policy=_POLICY,
        now=stable_start + 20,
    )
    assert gated_integrations(_SLUG, policy=_POLICY, now=stable_start + 20) == set()


def test_flapping_state_disk_round_trip() -> None:
    clear_flapping_cache()
    now = 5000.0
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
    assert "org/disk_mcp" in payload["integrations"]

    clear_flapping_cache()
    load_flapping_state_from_disk(_SLUG, payload, policy=_POLICY)
    gated = gated_integrations(_SLUG, policy=_POLICY, now=now + 20)
    assert gated == {("org", "disk_mcp")}


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
    assert gated_integrations(_SLUG, policy=disabled, now=now + 20) == set()


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
