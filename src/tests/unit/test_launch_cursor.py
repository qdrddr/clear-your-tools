"""Tests for ``cyt launch -- cursor``."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import patch

import pytest

from cyt.launch.cli import parse_launch_remainder
from cyt.launch.inject_via_prompt import CURSOR_PROXY_UNSUPPORTED_MESSAGE
from cyt.launch.upstream import ensure_upstream_for_runtime, resolve_upstream_kind


def test_parse_launch_remainder_accepts_cursor() -> None:
    agent, args = parse_launch_remainder(["--", "cursor", "."])
    assert agent == "cursor"
    assert args == ["."]


def test_resolve_upstream_kind_returns_none_for_cursor() -> None:
    assert resolve_upstream_kind(None, agent="cursor", explicit=None) is None


def test_ensure_upstream_for_runtime_skips_cursor() -> None:
    assert (
        ensure_upstream_for_runtime(
            agent="cursor",
            config_path=None,
            upstream_url=None,
            upstream_kind=None,
            upstream_name=None,
        )
        is None
    )


def test_run_cursor_launch_session_rejects_proxy_mode() -> None:
    from cyt.launch import cli as launch_cli
    from cyt.proxy.bootstrap import RuntimeContext

    runtime = RuntimeContext(
        config={
            "pruning": {"inject_via": {"cursor": "proxy", "claude": "proxy", "codex": "proxy"}},
        },
        config_path=Path("/tmp/config.yaml"),
        port=8834,
        credential_sources={},
        upstream_endpoint=None,
        upstream_url=None,
    )
    args = argparse.Namespace(
        debug=False,
        debug_dry_run=False,
        debug_strict=False,
    )

    with pytest.raises(SystemExit, match=CURSOR_PROXY_UNSUPPORTED_MESSAGE):
        launch_cli._run_cursor_launch_session(
            args=args,
            agent_args=["."],
            runtime=runtime,
        )


def test_run_cursor_launch_session_starts_hook_not_proxy() -> None:
    from cyt.launch import cli as launch_cli
    from cyt.proxy.bootstrap import RuntimeContext

    runtime = RuntimeContext(
        config={
            "pruning": {"inject_via": {"cursor": "hook", "claude": "hook", "codex": "hook"}},
            "skills": {"enabled": True},
        },
        config_path=Path("/tmp/config.yaml"),
        port=8834,
        credential_sources={},
        upstream_endpoint=None,
        upstream_url=None,
    )
    args = argparse.Namespace(
        debug=False,
        debug_dry_run=False,
        debug_strict=False,
    )

    with (
        patch.object(launch_cli, "sys") as mock_sys,
        patch.object(launch_cli, "ensure_tools_hook_file_interactive", side_effect=lambda _p, c: c),
        patch.object(launch_cli, "_ensure_hook_server") as ensure_hook,
        patch.object(launch_cli, "ensure_proxy") as ensure_proxy,
        patch.object(launch_cli, "print_runtime_env_report"),
        patch("cyt.agents.cursor.launch.run", return_value=0) as run_cursor,
        patch.object(launch_cli, "warm_caches", create=True),
    ):
        mock_sys.stdin.isatty.return_value = False
        with patch("cyt.cache.warm_caches"):
            code = launch_cli._run_cursor_launch_session(
                args=args,
                agent_args=["."],
                runtime=runtime,
            )

    assert code == 0
    ensure_proxy.assert_not_called()
    ensure_hook.assert_called_once()
    run_cursor.assert_called_once()
    assert run_cursor.call_args.kwargs.get("agent_args") == ["."]
