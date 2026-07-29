"""Tests for MCPC session flapping."""

from __future__ import annotations

from cyt.mcpc.session_flapping import (
    FlappingPolicy,
    clear_flapping_cache,
    gated_sessions,
    is_flapping,
    update_flapping_states,
)
from cyt.mcpc.session_health import SessionKey


def setup_function() -> None:
    clear_flapping_cache()


def test_is_flapping_detects_live_degraded_live_pattern() -> None:
    policy = FlappingPolicy(window_size=6, min_degraded=1, min_transitions=2)
    history = ["live", "connecting", "live"]
    assert is_flapping(history, policy=policy) is True


def test_update_flapping_states_gates_unstable_session() -> None:
    policy = FlappingPolicy(window_size=6, min_degraded=1, min_transitions=2)
    update_flapping_states(
        "slug",
        [{"name": "@ctx7", "status": "live"}],
        policy=policy,
        now=100.0,
    )
    update_flapping_states(
        "slug",
        [{"name": "@ctx7", "status": "connecting"}],
        policy=policy,
        now=101.0,
    )
    update_flapping_states(
        "slug",
        [{"name": "@ctx7", "status": "live"}],
        policy=policy,
        now=102.0,
    )
    gated = gated_sessions("slug", policy=policy, now=103.0)
    assert SessionKey(name="@ctx7") in gated
