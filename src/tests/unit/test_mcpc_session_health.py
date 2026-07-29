"""Tests for MCPC session health filtering."""

from __future__ import annotations

from unittest.mock import patch

from cyt.mcpc.session_health import (
    SessionHealthSnapshot,
    SessionKey,
    apply_health_snapshot,
    clear_session_health_cache,
    filter_catalog_by_session_health,
    refresh_session_health,
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


def test_refresh_session_health_restarts_non_live_sessions() -> None:
    initial = {"sessions": [{"name": "@context-mode", "status": "disconnected"}]}
    refreshed = {"sessions": [{"name": "@context-mode", "status": "live"}]}
    restart_calls: list[str] = []
    list_calls = {"count": 0}

    def fake_run_mcpc_json(_executable: str, args: list[str], **_kwargs: object) -> object | None:
        if args != []:
            return None
        list_calls["count"] += 1
        return initial if list_calls["count"] == 1 else refreshed

    def fake_restart(_exe: str, name: str, **_: object) -> bool:
        restart_calls.append(name)
        return True

    with (
        patch("cyt.mcpc.session_health.run_mcpc_json", side_effect=fake_run_mcpc_json),
        patch("cyt.mcpc.session_health.restart_mcpc_session", side_effect=fake_restart),
    ):
        snapshot = refresh_session_health(executable="mcpc", slug="mcpc")

    assert restart_calls == ["@context-mode"]
    assert SessionKey(name="@context-mode") in snapshot.live_sessions
    assert list_calls["count"] == 2
