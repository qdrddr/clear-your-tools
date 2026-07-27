"""Shared credential inspection and interactive setup for hook daemon flows."""

from __future__ import annotations

import sys
from typing import Any, cast

from cyt.config import (
    USER_ENV_PATH,
    required_proxy_env_var_names,
    required_tools_hook_env_var_names,
    tools_hook_executor_token_var,
    uses_cloudflare_tool_catalog,
    uses_executor_tool_catalog,
)
from cyt.launch.secrets import (
    _cwd_env_path,
    _user_env_path,
    ensure_wizard_credentials,
    inspect_named_credentials,
    preload_keyring_credentials,
)


def required_hook_daemon_env_var_names(config: dict[str, Any]) -> list[str]:
    """Return deduplicated env var names required for hook daemon credential injection."""
    return list(
        dict.fromkeys(
            [
                *required_proxy_env_var_names(config),
                *required_tools_hook_env_var_names(config),
            ],
        ),
    )


def _print_enabled_hook_catalog_sources(config: dict[str, Any]) -> None:
    if uses_executor_tool_catalog(config):
        token_var = tools_hook_executor_token_var(config)
        print(f"Executor tool catalog: enabled ({token_var})")
    if uses_cloudflare_tool_catalog(config):
        from cyt.config import (
            tools_hook_cloudflare_access_client_id_var,
            tools_hook_cloudflare_access_client_secret_var,
        )

        print(
            "Cloudflare tool catalog: enabled ("
            f"{tools_hook_cloudflare_access_client_id_var(config)}, "
            f"{tools_hook_cloudflare_access_client_secret_var(config)})",
        )


def _handle_missing_non_tty_credentials(
    missing_before: list[str],
    *,
    exit_on_missing_non_tty: bool,
) -> None:
    vars_block = "\n".join(f"\t{name}" for name in missing_before)
    env_locations = "\n".join(f"\t{p}" for p in (_cwd_env_path(), _user_env_path()))
    message = (
        f"Required environment variable(s) not set:\n{vars_block}\n"
        f"Export them in the shell or define them in\n{env_locations}\n"
        "Or run interactively to store them in the keyring."
    )
    if exit_on_missing_non_tty:
        raise SystemExit(message)
    print(message)


def report_and_ensure_hook_credentials(
    config: dict[str, Any],
    *,
    exit_on_missing_non_tty: bool = False,
) -> dict[str, str | None]:
    """Inspect hook-daemon credentials and interactively persist any missing values."""
    names = required_hook_daemon_env_var_names(config)
    if not names:
        print("Hook credentials: none required for the current pipeline.")
        return {}

    _print_enabled_hook_catalog_sources(config)

    preload_keyring_credentials(names)

    print("Checking required API keys:")
    before_sources = dict(inspect_named_credentials(names, allow_prompt=False))
    for name in names:
        source = before_sources.get(name)
        if source:
            print(f"  {name}: {source}")
        else:
            print(f"  {name}: missing")

    missing_before = [name for name in names if not before_sources.get(name)]
    if not missing_before:
        print("All required keys are already available.")
        return before_sources

    if missing_before and not sys.stdin.isatty():
        _handle_missing_non_tty_credentials(
            missing_before,
            exit_on_missing_non_tty=exit_on_missing_non_tty,
        )
        return before_sources

    sources = ensure_wizard_credentials(names, env_fallback_path=USER_ENV_PATH)
    persisted = [
        name for name in names if sources.get(name) and sources[name] != before_sources.get(name)
    ]
    if persisted:
        print("Updated credentials:")
        for name in persisted:
            print(f"  {name}: {sources[name]}")
    return cast(dict[str, str | None], sources)
