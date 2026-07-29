"""Tests for ``cyt launch -- cursor``."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import patch

import pytest

from cyt.agents.cursor.launch import ensure_cursor_inject_via_hook
from cyt.config import inject_via
from cyt.launch.cli import parse_launch_remainder
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


def test_ensure_cursor_inject_via_hook_noop_when_already_hook(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("pruning:\n  inject_via: hook\n", encoding="utf-8")
    from cyt.config import load_config

    config = load_config(config_path)
    updated = ensure_cursor_inject_via_hook(config_path, config)
    assert inject_via(updated) == "hook"


def test_ensure_cursor_inject_via_hook_prompts_and_saves(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("pruning:\n  inject_via: proxy\n", encoding="utf-8")
    from cyt.config import load_config

    monkeypatch.setattr("cyt.agents.cursor.launch.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("cyt.agents.cursor.launch._prompt_yes_no", lambda *_a, **_k: True)

    config = load_config(config_path)
    updated = ensure_cursor_inject_via_hook(config_path, config)
    assert inject_via(updated) == "hook"
    saved = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert "inject_via: hook" in saved


def test_ensure_cursor_inject_via_hook_exits_when_user_declines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("pruning:\n  inject_via: proxy\n", encoding="utf-8")
    from cyt.config import load_config

    monkeypatch.setattr("cyt.agents.cursor.launch.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("cyt.agents.cursor.launch._prompt_yes_no", lambda *_a, **_k: False)

    with pytest.raises(SystemExit, match=r"requires pruning\.inject_via: hook"):
        ensure_cursor_inject_via_hook(config_path, load_config(config_path))


def test_run_cursor_launch_session_starts_hook_not_proxy() -> None:
    from cyt.launch import cli as launch_cli
    from cyt.proxy.bootstrap import RuntimeContext

    runtime = RuntimeContext(
        config={"pruning": {"inject_via": "hook"}, "skills": {"enabled": True}},
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
        patch.object(launch_cli, "ensure_cursor_inject_via_hook", side_effect=lambda _p, c: c),
        patch.object(launch_cli, "ensure_cursor_hooks_for_launch"),
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
