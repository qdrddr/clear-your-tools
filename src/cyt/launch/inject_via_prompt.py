"""Interactive ``pruning.inject_via`` alignment for launch and hook setup."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Literal

from cyt.config import inject_via, save_user_config, sync_config_in_place
from cyt.proxy.setup_wizard import _prompt_yes_no

InjectViaMode = Literal["hook", "proxy"]


def _stop_for_inject_via_mode(mode: InjectViaMode, *, verbose: bool = False) -> None:
    if mode == "hook":
        from cyt.stop import stop_proxies_for_hook_setup

        stop_proxies_for_hook_setup(verbose=verbose)
        return

    from cyt.hook.daemon import daemon_stop

    daemon_stop(verbose=verbose)


def _reconfigure_modules_for_inject_via(config: dict[str, Any]) -> None:
    from cyt.cache import warm_caches
    from cyt.pruners.policies import configure_policies_from_config

    configure_policies_from_config(config)
    warm_caches(config)


def _start_runtime_for_inject_via(
    target: InjectViaMode,
    *,
    config_path: Path,
    verbose: bool,
) -> None:
    if target != "hook":
        return
    from cyt.hook.daemon import daemon_start

    daemon_start(
        config_path=config_path,
        verbose=verbose,
        unattended=not sys.stdin.isatty(),
    )


def apply_inject_via_switch(
    config_path: Path,
    config: dict[str, Any],
    *,
    target: InjectViaMode,
    verbose: bool = False,
    start_runtime: bool = False,
) -> dict[str, Any]:
    """Persist ``inject_via``, refresh in-memory config, and align live services."""
    save_user_config(
        config_path,
        {"pruning": {"inject_via": target}},
        apply_bundled_sections=False,
    )
    sync_config_in_place(config, config_path)
    _stop_for_inject_via_mode(target, verbose=verbose)
    if start_runtime:
        _start_runtime_for_inject_via(target, config_path=config_path, verbose=verbose)
    _reconfigure_modules_for_inject_via(config)
    return config


def _ensure_inject_via(
    config_path: Path,
    config: dict[str, Any],
    *,
    target: InjectViaMode,
    prompt: str,
    decline_exit_message: str | None = None,
    start_runtime: bool = False,
) -> dict[str, Any]:
    if inject_via(config) == target:
        return config

    if not sys.stdin.isatty():
        if decline_exit_message is not None:
            raise SystemExit(decline_exit_message)
        return config

    if not _prompt_yes_no(prompt, default_yes=True):
        if decline_exit_message is not None:
            raise SystemExit(decline_exit_message)
        return config

    return apply_inject_via_switch(
        config_path,
        config,
        target=target,
        start_runtime=start_runtime,
    )


def _prompt_uninstall_cyt_hooks_if_installed() -> None:
    from cyt.hook.setup_wizard import cyt_hooks_installed, run_hook_uninstall

    if not cyt_hooks_installed():
        return
    if not _prompt_yes_no(
        "CYT agent hooks are still installed in Claude/Codex/Cursor configs. "
        "Remove them to avoid conflicts with proxy injection?",
        default_yes=True,
    ):
        return
    run_hook_uninstall()


def ensure_launch_inject_via_proxy(
    config_path: Path,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Ensure proxy injection before Claude/Codex launch."""
    if inject_via(config) != "proxy":
        previous_mode = inject_via(config)
        config = _ensure_inject_via(
            config_path,
            config,
            target="proxy",
            prompt=("Agent launch uses proxy injection. Switch pruning.inject_via to proxy?"),
        )
        if inject_via(config) == "proxy" and previous_mode != "proxy":
            _prompt_uninstall_cyt_hooks_if_installed()
    else:
        _stop_for_inject_via_mode("proxy")
    return config


def _prompt_stop_running_proxies_if_needed(config_path: Path) -> None:
    from cyt.hook.daemon import daemon_start
    from cyt.stop import proxies_conflicting_with_hook_setup, stop_proxies_for_hook_setup

    if not proxies_conflicting_with_hook_setup():
        return
    if not sys.stdin.isatty():
        return
    if not _prompt_yes_no(
        "A CYT reverse proxy is still running. Stop it to avoid conflicts with hook injection?",
        default_yes=True,
    ):
        return
    stop_proxies_for_hook_setup(verbose=False)
    daemon_start(
        config_path=config_path,
        verbose=False,
        unattended=not sys.stdin.isatty(),
    )


def ensure_hook_inject_via(
    config_path: Path,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Ensure hook injection before installing agent hooks."""
    already_hook = inject_via(config) == "hook"
    config = _ensure_inject_via(
        config_path,
        config,
        target="hook",
        prompt=("This installation uses hook injection. Switch pruning.inject_via to hook?"),
        start_runtime=True,
    )
    if already_hook:
        _prompt_stop_running_proxies_if_needed(config_path)
    return config


CURSOR_PROXY_UNSUPPORTED_MESSAGE = (
    "cursor does not support proxy, please use hooks instead, run: \n\tcyt hook cursor"
)
