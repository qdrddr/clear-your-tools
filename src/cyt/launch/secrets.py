"""Credential resolution with env, keyring, and interactive prompt."""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path
from typing import Any

from cyt.config import CWD_ENV_PATH, USER_ENV_PATH, load_proxy_env, required_proxy_env_var_names
from cyt.launch.config import required_launch_env_var_names
from cyt.launch.upstream import AgentName
from cyt.proxy.setup import parse_env_file

KEYRING_SERVICE = "cyt"


def _env_file_source(path: Path) -> str:
    expanded = path.expanduser()
    if expanded == CWD_ENV_PATH:
        return "env: ./.env"
    if expanded == USER_ENV_PATH:
        return "env: ~/.config/cyt/.env"
    return f"env: {expanded}"


def _snapshot_env() -> dict[str, str]:
    return dict(os.environ)


def _detect_env_source(name: str, before: dict[str, str]) -> str | None:
    if name not in os.environ:
        return None
    if before.get(name) == os.environ.get(name) and name in before:
        return "env: shell"
    if name in parse_env_file(CWD_ENV_PATH):
        return _env_file_source(CWD_ENV_PATH)
    if name in parse_env_file(USER_ENV_PATH):
        return _env_file_source(USER_ENV_PATH)
    return "env"


def _read_keyring(name: str) -> str | None:
    try:
        import keyring
    except ImportError:
        return None
    try:
        value = keyring.get_password(KEYRING_SERVICE, name)
    except Exception:
        return None
    if value:
        return value
    return None


def _write_keyring(name: str, value: str) -> None:
    try:
        import keyring
    except ImportError:
        return
    try:
        keyring.set_password(KEYRING_SERVICE, name, value)
    except Exception:
        return


def delete_keyring_secret(name: str) -> None:
    """Remove a key from the cyt keyring service."""
    try:
        import keyring
    except ImportError:
        return
    try:
        keyring.delete_password(KEYRING_SERVICE, name)
    except Exception:
        return


def resolve_credential(
    name: str,
    *,
    before_env: dict[str, str],
    allow_prompt: bool = True,
) -> tuple[str | None, str | None]:
    """Return (value, source) for *name* without printing secrets."""
    if source := _detect_env_source(name, before_env):
        return os.environ[name], source

    if value := _read_keyring(name):
        os.environ[name] = value
        return value, "keyring"

    if not allow_prompt:
        return None, None

    if not sys.stdin.isatty():
        return None, None

    value = getpass.getpass(f"{name}: ")
    if not value:
        return None, None
    os.environ[name] = value
    _write_keyring(name, value)
    return value, "prompt"


def required_env_var_names(
    config: dict[str, Any],
    *,
    agent: AgentName | None,
    endpoint: str | None = None,
) -> list[str]:
    """Return env var names required for proxy or launch."""
    if agent is None:
        return required_proxy_env_var_names(config)
    return required_launch_env_var_names(config, agent, endpoint=endpoint)


def ensure_runtime_credentials(
    config: dict[str, Any],
    *,
    agent: AgentName | None,
    credential_sources: dict[str, str],
    endpoint: str | None = None,
) -> None:
    """Ensure required credentials are available; populate *credential_sources*."""
    before = _snapshot_env()
    load_proxy_env()

    names = required_env_var_names(config, agent=agent, endpoint=endpoint)
    missing: list[str] = []
    for name in names:
        if os.environ.get(name):
            if source := _detect_env_source(name, before):
                credential_sources[name] = source
            continue
        value, source = resolve_credential(name, before_env=before)
        if value and source:
            credential_sources[name] = source
        else:
            missing.append(name)

    if missing:
        vars_block = "\n".join(f"\t{name}" for name in missing)
        env_locations = "\n".join(f"\t{p}" for p in (CWD_ENV_PATH, USER_ENV_PATH))
        raise SystemExit(
            f"Required environment variable(s) not set:\n{vars_block}\n"
            f"Export them in the shell or define them in\n{env_locations}\n"
            "Or run interactively to store them in the keyring.",
        )
