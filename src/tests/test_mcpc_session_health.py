"""Tests for MCPC session health filtering."""

from __future__ import annotations

from cyt.mcpc.session_health import (
    SessionHealthSnapshot,
    SessionKey,
    apply_health_snapshot,
    clear_session_health_cache,
    filter_catalog_by_session_health,
    sessions_list_to_dict,
)


def setup_function() -> None:
    clear_session_health_cache()


def test_filter_catalog_by_session_health_drops_non_live() -> None:
    slug = "mcpc"
    sessions = sessions_list_to_dict(
        [
            {"name": "@ctx7", "status": "live"},
            {"name": "@hedl", "status": "connecting"},
        ],
    )
    apply_health_snapshot(
        slug,
        SessionHealthSnapshot(
            sessions=sessions,
            live_sessions={SessionKey(name="@ctx7")},
            updated_at=1.0,
            loaded=True,
        ),
    )
    tools = [
        {"name": "@ctx7/resolve-library-id", "mcpc_session": "@ctx7"},
        {"name": "@hedl/demo", "mcpc_session": "@hedl"},
    ]
    filtered = filter_catalog_by_session_health(tools, slug)
    assert [tool["name"] for tool in filtered] == ["@ctx7/resolve-library-id"]
