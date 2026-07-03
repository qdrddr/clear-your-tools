"""Tests for launch skipping proxy when hook modes allow direct upstream."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from cyt.config import launch_needs_proxy


def test_launch_needs_proxy_when_inject_via_proxy() -> None:
    config = {
        "pruning": {"inject_via": "proxy"},
        "skills": {"enabled": True},
    }
    assert launch_needs_proxy(config) is True


def test_launch_skips_proxy_when_inject_via_hook() -> None:
    config = {
        "pruning": {"inject_via": "hook"},
        "skills": {"enabled": True},
    }
    assert launch_needs_proxy(config) is False


def test_launch_skips_proxy_when_skills_disabled_and_hook_mode() -> None:
    config = {
        "pruning": {"inject_via": "hook"},
        "skills": {"enabled": False},
    }
    assert launch_needs_proxy(config) is False


def test_launch_needs_proxy_legacy_tools_inject_via_proxy() -> None:
    config = {
        "pruning": {"tools": {"inject_via": "proxy"}},
        "skills": {"enabled": True},
    }
    assert launch_needs_proxy(config) is True


def test_run_launch_session_skips_proxy_when_not_needed() -> None:
    from cyt.launch import cli as launch_cli

    runtime = MagicMock()
    runtime.config = {
        "pruning": {"inject_via": "hook"},
        "skills": {"enabled": True},
    }
    runtime.config_path = MagicMock()
    runtime.port = 8787
    runtime.credential_sources = {}
    runtime.upstream_url = None

    args = MagicMock()
    args.debug = False
    args.debug_dry_run = False
    args.debug_strict = False

    with (
        patch.object(launch_cli, "sys") as mock_sys,
        patch.object(launch_cli, "ensure_tools_hook_file_interactive", side_effect=lambda _p, c: c),
        patch.object(launch_cli, "ensure_proxy") as ensure_proxy,
        patch.object(launch_cli, "require_healthy_proxy") as require_healthy,
        patch.object(launch_cli, "_ensure_launch_agent_auth", return_value=(runtime, None)),
        patch.object(launch_cli, "print_runtime_env_report"),
        patch.object(launch_cli, "_run_launched_agent", return_value=0),
        patch.object(launch_cli, "_launch_debug_flags", return_value=(False, False, False)),
        patch.object(launch_cli, "launch_agent_env", return_value={}),
    ):
        mock_sys.stdin.isatty.return_value = False
        launch_cli._run_launch_session(
            args=args,
            agent="claude",
            agent_args=[],
            runtime=runtime,
            endpoint="anthropic",
        )

    ensure_proxy.assert_not_called()
    require_healthy.assert_not_called()
