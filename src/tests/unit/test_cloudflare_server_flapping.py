"""Cloudflare server flapping disk persistence."""

from __future__ import annotations

import time

from cyt.cloudflare.server_flapping import (
    ServerFlapState,
    clear_flapping_cache,
    flapping_state_to_disk,
    load_flapping_state_from_disk,
)


def test_flapping_state_disk_round_trip_preserves_quarantine() -> None:
    clear_flapping_cache()
    slug = "https___mcp.example.com"
    state = ServerFlapState(
        gated=True,
        quarantine_until=time.monotonic() + 120.0,
        consecutive_healthy=0,
        flap_episodes=2,
    )
    state.status_history.append("enabled")
    state.status_history.append("disabled")

    from cyt.cloudflare import server_flapping as flapping_mod

    with flapping_mod._flapping_lock:
        flapping_mod._flapping_states[slug] = {"context7": state}

    payload = flapping_state_to_disk(slug)
    quarantine_value = payload["servers"]["context7"]["quarantine_until"]
    assert isinstance(quarantine_value, str)
    assert "T" in quarantine_value

    clear_flapping_cache()
    load_flapping_state_from_disk(slug, payload)

    with flapping_mod._flapping_lock:
        restored = flapping_mod._flapping_states[slug]["context7"]

    assert restored.gated is True
    assert restored.quarantine_until > time.monotonic()
    assert restored.flap_episodes == 2


def test_flapping_state_load_ignores_legacy_monotonic_quarantine() -> None:
    clear_flapping_cache()
    slug = "legacy"
    load_flapping_state_from_disk(
        slug,
        {
            "servers": {
                "context7": {
                    "gated": True,
                    "quarantine_until": 999999.0,
                    "consecutive_healthy": 0,
                    "flap_episodes": 1,
                    "status_history": ["disabled"],
                },
            },
        },
    )

    from cyt.cloudflare import server_flapping as flapping_mod

    with flapping_mod._flapping_lock:
        restored = flapping_mod._flapping_states[slug]["context7"]

    assert restored.quarantine_until == 0.0
