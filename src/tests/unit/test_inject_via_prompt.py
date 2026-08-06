"""Tests for interactive pruning.inject_via alignment."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from cyt.config import inject_via, inject_via_for_agent, load_config, sync_config_in_place
from cyt.launch import inject_via_prompt

_MAP_ALL_HOOK = """pruning:
  inject_via:
    cursor: hook
    claude: hook
    codex: hook
"""

_MAP_DEFAULT = """pruning:
  inject_via:
    cursor: hook
    claude: proxy
    codex: proxy
"""

_MAP_ALL_PROXY = _MAP_DEFAULT


def test_sync_config_in_place_updates_existing_reference(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_MAP_ALL_HOOK, encoding="utf-8")
    config = load_config(config_path)
    holder = config

    config_path.write_text(_MAP_ALL_PROXY, encoding="utf-8")
    sync_config_in_place(config, config_path)

    assert holder is config
    assert inject_via_for_agent(holder, "claude") == "proxy"


def test_ensure_launch_inject_via_proxy_stops_hook_daemon_when_already_proxy(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_MAP_ALL_PROXY, encoding="utf-8")
    config = load_config(config_path)

    with patch("cyt.launch.inject_via_prompt._stop_for_inject_via_mode") as stop:
        updated = inject_via_prompt.ensure_launch_inject_via_proxy(config_path, config)

    stop.assert_called_once_with("proxy")
    assert inject_via_for_agent(updated, "claude") == "proxy"


def test_ensure_launch_inject_via_proxy_prompts_saves_and_stops_daemon(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_MAP_ALL_HOOK, encoding="utf-8")
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
    assert inject_via_for_agent(updated, "claude") == "proxy"
    assert "claude: proxy" in config_path.read_text(encoding="utf-8")


def test_ensure_launch_inject_via_proxy_keeps_hook_when_declined(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_MAP_ALL_HOOK, encoding="utf-8")
    monkeypatch.setattr("cyt.launch.inject_via_prompt.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("cyt.launch.inject_via_prompt._prompt_yes_no", lambda *_a, **_k: False)

    with patch("cyt.hook.daemon.daemon_stop") as daemon_stop:
        updated = inject_via_prompt.ensure_launch_inject_via_proxy(
            config_path,
            load_config(config_path),
        )

    daemon_stop.assert_not_called()
    assert inject_via(updated) == "hook"
    assert "claude: hook" in config_path.read_text(encoding="utf-8")


def test_ensure_hook_inject_via_prompts_saves_stops_proxies_and_starts_daemon(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_MAP_ALL_PROXY, encoding="utf-8")
    monkeypatch.setattr("cyt.launch.inject_via_prompt.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("cyt.launch.inject_via_prompt._prompt_yes_no", lambda *_a, **_k: True)
    config = load_config(config_path)

    with (
        patch("cyt.stop.stop_proxies_for_hook_setup") as stop_proxies,
        patch("cyt.stop.proxies_are_running", return_value=False),
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
    assert "cursor: hook" in config_path.read_text(encoding="utf-8")


def test_ensure_hook_inject_via_prompts_to_stop_proxies_when_already_hook(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_MAP_ALL_HOOK, encoding="utf-8")
    monkeypatch.setattr("cyt.launch.inject_via_prompt.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(
        "cyt.launch.inject_via_prompt._prompt_yes_no",
        lambda *_a, **_k: True,
    )
    config = load_config(config_path)

    with (
        patch("cyt.stop.proxies_conflicting_with_hook_setup", return_value=True),
        patch("cyt.stop.stop_proxies_for_hook_setup") as stop_proxies,
        patch("cyt.hook.daemon.daemon_start") as daemon_start,
    ):
        inject_via_prompt.ensure_hook_inject_via(config_path, config)

    stop_proxies.assert_called_once_with(verbose=False)
    daemon_start.assert_called_once_with(
        config_path=config_path,
        verbose=False,
        unattended=False,
    )


def test_ensure_hook_inject_via_skips_proxy_stop_prompt_when_none_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_MAP_ALL_HOOK, encoding="utf-8")
    monkeypatch.setattr("cyt.launch.inject_via_prompt.sys.stdin.isatty", lambda: True)

    with (
        patch("cyt.stop.proxies_conflicting_with_hook_setup", return_value=False),
        patch("cyt.stop.stop_proxies_for_hook_setup") as stop_proxies,
        patch("cyt.launch.inject_via_prompt._prompt_yes_no") as prompt,
    ):
        inject_via_prompt.ensure_hook_inject_via(config_path, load_config(config_path))

    stop_proxies.assert_not_called()
    prompt.assert_not_called()


def test_ensure_launch_inject_via_proxy_prompts_hook_uninstall_after_switch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_MAP_ALL_HOOK, encoding="utf-8")
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
    assert inject_via_for_agent(updated, "claude") == "proxy"


def test_ensure_launch_inject_via_proxy_skips_hook_uninstall_when_not_installed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_MAP_ALL_HOOK, encoding="utf-8")
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
