"""Tests for interactive pruning.inject_via alignment."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from cyt.config import inject_via, load_config, sync_config_in_place
from cyt.launch import inject_via_prompt


def test_sync_config_in_place_updates_existing_reference(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("pruning:\n  inject_via: hook\n", encoding="utf-8")
    config = load_config(config_path)
    holder = config

    config_path.write_text("pruning:\n  inject_via: proxy\n", encoding="utf-8")
    sync_config_in_place(config, config_path)

    assert holder is config
    assert inject_via(holder) == "proxy"


def test_ensure_launch_inject_via_proxy_stops_hook_daemon_when_already_proxy(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("pruning:\n  inject_via: proxy\n", encoding="utf-8")
    config = load_config(config_path)

    with patch("cyt.launch.inject_via_prompt._stop_for_inject_via_mode") as stop:
        updated = inject_via_prompt.ensure_launch_inject_via_proxy(config_path, config)

    stop.assert_called_once_with("proxy")
    assert inject_via(updated) == "proxy"


def test_ensure_launch_inject_via_proxy_prompts_saves_and_stops_daemon(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("pruning:\n  inject_via: hook\n", encoding="utf-8")
    monkeypatch.setattr("cyt.launch.inject_via_prompt.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("cyt.launch.inject_via_prompt._prompt_yes_no", lambda *_a, **_k: True)
    config = load_config(config_path)

    with (
        patch("cyt.hook.daemon.daemon_stop") as daemon_stop,
        patch("cyt.launch.inject_via_prompt._reconfigure_modules_for_inject_via") as reconfigure,
    ):
        updated = inject_via_prompt.ensure_launch_inject_via_proxy(config_path, config)

    daemon_stop.assert_called_once_with(verbose=False)
    reconfigure.assert_called_once_with(config)
    assert updated is config
    assert inject_via(updated) == "proxy"
    assert "inject_via: proxy" in config_path.read_text(encoding="utf-8")


def test_ensure_launch_inject_via_proxy_keeps_hook_when_declined(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("pruning:\n  inject_via: hook\n", encoding="utf-8")
    monkeypatch.setattr("cyt.launch.inject_via_prompt.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("cyt.launch.inject_via_prompt._prompt_yes_no", lambda *_a, **_k: False)

    with patch("cyt.hook.daemon.daemon_stop") as daemon_stop:
        updated = inject_via_prompt.ensure_launch_inject_via_proxy(
            config_path,
            load_config(config_path),
        )

    daemon_stop.assert_not_called()
    assert inject_via(updated) == "hook"
    assert "inject_via: hook" in config_path.read_text(encoding="utf-8")


def test_ensure_hook_inject_via_prompts_saves_stops_proxies_and_starts_daemon(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("pruning:\n  inject_via: proxy\n", encoding="utf-8")
    monkeypatch.setattr("cyt.launch.inject_via_prompt.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("cyt.launch.inject_via_prompt._prompt_yes_no", lambda *_a, **_k: True)
    config = load_config(config_path)

    with (
        patch("cyt.stop.stop_proxies_only") as stop_proxies,
        patch("cyt.hook.daemon.daemon_start") as daemon_start,
        patch("cyt.launch.inject_via_prompt._reconfigure_modules_for_inject_via") as reconfigure,
    ):
        updated = inject_via_prompt.ensure_hook_inject_via(config_path, config)

    stop_proxies.assert_called_once_with(verbose=False)
    daemon_start.assert_called_once_with(
        config_path=config_path,
        verbose=False,
        unattended=False,
    )
    reconfigure.assert_called_once_with(config)
    assert updated is config
    assert inject_via(updated) == "hook"
    assert "inject_via: hook" in config_path.read_text(encoding="utf-8")


def test_ensure_launch_inject_via_proxy_prompts_hook_uninstall_after_switch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("pruning:\n  inject_via: hook\n", encoding="utf-8")
    monkeypatch.setattr("cyt.launch.inject_via_prompt.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(
        "cyt.launch.inject_via_prompt._prompt_yes_no",
        lambda *_a, **_k: True,
    )
    config = load_config(config_path)

    with (
        patch("cyt.hook.daemon.daemon_stop"),
        patch("cyt.launch.inject_via_prompt._reconfigure_modules_for_inject_via"),
        patch("cyt.hook.setup_wizard.cyt_hooks_installed", return_value=True),
        patch("cyt.hook.setup_wizard.run_hook_uninstall") as uninstall,
    ):
        updated = inject_via_prompt.ensure_launch_inject_via_proxy(config_path, config)

    uninstall.assert_called_once()
    assert inject_via(updated) == "proxy"


def test_ensure_launch_inject_via_proxy_skips_hook_uninstall_when_not_installed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("pruning:\n  inject_via: hook\n", encoding="utf-8")
    monkeypatch.setattr("cyt.launch.inject_via_prompt.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(
        "cyt.launch.inject_via_prompt._prompt_yes_no",
        lambda *_a, **_k: True,
    )

    with (
        patch("cyt.hook.daemon.daemon_stop"),
        patch("cyt.launch.inject_via_prompt._reconfigure_modules_for_inject_via"),
        patch("cyt.hook.setup_wizard.cyt_hooks_installed", return_value=False),
        patch("cyt.hook.setup_wizard.run_hook_uninstall") as uninstall,
    ):
        inject_via_prompt.ensure_launch_inject_via_proxy(
            config_path,
            load_config(config_path),
        )

    uninstall.assert_not_called()
